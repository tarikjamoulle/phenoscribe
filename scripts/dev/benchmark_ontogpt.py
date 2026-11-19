"""Benchmark ontoGPT vs Phenoscribe on the same pseudonymised transcripts.

Setup (one time, in a separate virtual environment). The `setuptools<81` pin is
necessary: newer setuptools has no `pkg_resources`, which oaklib's eutils
dependency imports.

    uv venv .venv-ontogpt
    VIRTUAL_ENV=$(pwd)/.venv-ontogpt uv pip install ontogpt "setuptools<81"

Two upstream issues need patching once after install:
    1. ontoGPT 1.1.1's `HumanPhenotypeSet` pydantic model has `extra='forbid'`
       but the SPIRES engine emits `original_spans` — every extract crashes.
       Fix:
           sed -i.bak "s/extra = 'forbid'/extra = 'allow'/g" \\
               .venv-ontogpt/lib/python3.*/site-packages/ontogpt/templates/human_phenotype.py
    2. oaklib's HPO SQLite adapter must be warmed once (auto-downloads):
           .venv-ontogpt/bin/python -c "from oaklib import get_adapter; \\
               get_adapter('sqlite:obo:hp').labels(['HP:0012378'])"
    Without (2), ontoGPT silently falls back to AUTO:phrase IDs instead of HP:codes.

Needs private data: pseudonymised transcripts in output/pseudo/ and a results
workbook in output/results.xlsx.

Usage:
    uv run python scripts/dev/benchmark_ontogpt.py [--limit N]

Outputs:
    output/ontogpt-benchmark.md
"""

import argparse
import json
import logging
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

from phenoscribe.aggregate import load_patient_codes
from phenoscribe.config import LLMConfig
from phenoscribe.llm import llm_call
from phenoscribe.semantic_similarity import get_ic_map, lin_similarity
from phenoscribe.validate import load_hpo

# Lin IC-similarity threshold for "close" credit. Under Lin similarity a
# near-root prediction scores ~0, so it never counts as a near-hit to a
# specific code.
CLOSE_TAU = 0.7

PSEUDO_DIR = Path("output/pseudo")
ONTOGPT_BIN = Path(".venv-ontogpt/bin/ontogpt")
TMP_DIR = Path(__file__).resolve().parents[2] / ".tmp" / "ontogpt_bench"
PHENOSCRIBE_RESULTS = "output/results.xlsx"
REPORT_PATH = Path("output/ontogpt-benchmark.md")

TRANSLATE_SYSTEM = (
    "You translate French clinical interview transcripts into English. Preserve "
    "every symptom and patient quote literally. The transcript is already pseudonymised "
    "(PERSON_1, ORGANIZATION_1, MISC_4 etc.) — leave those tokens untouched. Output only "
    "the English translation, no commentary."
)

logger = logging.getLogger("benchmark_ontogpt")


_PSEUDONYM_TOKEN = re.compile(r"(?:PERSON|ORGANIZATION|LOCATION|DATE|MISC)_\d+")


def _check_pseudonyms_preserved(fr: str, en: str, patient_id: str) -> None:
    """Warn if the LLM dropped any pseudonymisation tokens during translation.

    A cached `en.txt` with a dropped token can hold a real value, so the gap
    gets a warning.
    """
    fr_tokens = set(_PSEUDONYM_TOKEN.findall(fr))
    en_tokens = set(_PSEUDONYM_TOKEN.findall(en))
    missing = fr_tokens - en_tokens
    if missing:
        logger.warning(
            "[%s] translation dropped %d pseudonym token(s): %s",
            patient_id, len(missing), sorted(missing),
        )


def translate(fr_text: str, patient_id: str, force: bool = False) -> str:
    """French → English translation, cached per patient to avoid re-running."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cache = TMP_DIR / f"{patient_id}.en.txt"
    if not force and cache.exists():
        en = cache.read_text()
    else:
        en = llm_call(
            system_prompt=TRANSLATE_SYSTEM,
            user_prompt=fr_text,
            llm=LLMConfig(provider="openai", model="gpt-4o"),
        )
        cache.write_text(en)
    _check_pseudonyms_preserved(fr_text, en, patient_id)
    return en


def run_ontogpt(en_text: str, patient_id: str, force: bool = False) -> set[str]:
    """Run ontoGPT extract on English text, return the set of grounded HP: IDs.

    Caches results in TMP_DIR so re-runs of the report don't re-burn API budget.
    """
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    in_path = TMP_DIR / f"{patient_id}.en.txt"
    out_path = TMP_DIR / f"{patient_id}.json"
    in_path.write_text(en_text)
    if force or not out_path.exists():
        result = subprocess.run(
            [
                str(ONTOGPT_BIN),
                "extract",
                "-i", str(in_path),
                "-t", "human_phenotype",
                "-m", "gpt-4o",
                "-O", "json",
                "-o", str(out_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.error(
                "[%s] ontogpt extract failed (exit %d). stderr tail:\n%s",
                patient_id, result.returncode, result.stderr[-2000:],
            )
            result.check_returncode()
    data = json.loads(out_path.read_text())
    return {
        ne["id"]
        for ne in data.get("named_entities", [])
        if isinstance(ne.get("id"), str) and ne["id"].startswith("HP:")
    }


def find_unknown_codes(codes: set[str], hpo) -> set[str]:
    """Return the subset of `codes` that aren't recognised by the loaded HPO ontology.

    A code present in Phenoscribe's ChromaDB but unknown to hpo-toolkit's
    cached release means the two HPO snapshots disagree.
    """
    unknown = set()
    for code in codes:
        try:
            term = hpo.get_term(code)
        except Exception:
            unknown.add(code)
            continue
        if term is None:
            unknown.add(code)
    return unknown


def categorise(predicted: set[str], reference: set[str], hpo, ic_map,
               tau: float = CLOSE_TAU):
    """Split `predicted` into exact / close / unique with respect to `reference`.

    "close" is IC-based: a predicted code is close to a reference code if their
    Lin (1998) information-content similarity is >= `tau` (as in
    `validate.py`). A near-root prediction scores ~0, because the most
    informative common ancestor carries almost no information.
    """
    exact = predicted & reference
    remaining = predicted - exact
    close = set()
    for p in remaining:
        for r in reference:
            if lin_similarity(ic_map, hpo, p, r) >= tau:
                close.add(p)
                break
    unique = remaining - close
    return exact, close, unique


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N transcripts")
    parser.add_argument("--force", action="store_true", help="Bypass the translation + ontoGPT cache in .tmp/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if not ONTOGPT_BIN.exists():
        logger.error("ontoGPT binary not found at %s — run the setup commands in the script docstring.", ONTOGPT_BIN)
        sys.exit(1)

    phenoscribe_codes = load_patient_codes(PHENOSCRIBE_RESULTS)
    transcripts = sorted(PSEUDO_DIR.glob("*.txt"))
    if args.limit:
        transcripts = transcripts[: args.limit]

    hpo = load_hpo()
    ic_map = get_ic_map(hpo)
    per_patient = []

    for tx in transcripts:
        pid = tx.stem
        logger.info("[%s] translating French → English%s", pid, " (cached)" if (TMP_DIR / f"{pid}.en.txt").exists() and not args.force else "")
        en = translate(tx.read_text(), pid, force=args.force)
        logger.info("[%s] running ontoGPT extract%s", pid, " (cached)" if (TMP_DIR / f"{pid}.json").exists() and not args.force else "")
        ontogpt_ids = run_ontogpt(en, pid, force=args.force)
        ph_ids = {row["hpo_id"] for row in phenoscribe_codes.get(pid, [])}
        ph_exact, ph_close, ph_unique = categorise(ph_ids, ontogpt_ids, hpo, ic_map)
        _, og_close, og_unique = categorise(ontogpt_ids, ph_ids, hpo, ic_map)
        logger.info(
            "[%s] phenoscribe=%d ontogpt=%d exact=%d ph_close=%d og_close=%d ph_unique=%d og_unique=%d",
            pid, len(ph_ids), len(ontogpt_ids),
            len(ph_exact), len(ph_close), len(og_close), len(ph_unique), len(og_unique),
        )
        per_patient.append({
            "pid": pid,
            "phenoscribe_ids": ph_ids,
            "ontogpt_ids": ontogpt_ids,
            "exact_overlap": ph_exact,
            "phenoscribe_close": ph_close,
            "ontogpt_close": og_close,
            "phenoscribe_unique": ph_unique,
            "ontogpt_unique": og_unique,
            "phenoscribe_unknown_to_hpotk": find_unknown_codes(ph_ids, hpo),
        })

    write_report(per_patient)


def write_report(per_patient: list[dict]) -> None:
    total_ph = sum(len(p["phenoscribe_ids"]) for p in per_patient)
    total_og = sum(len(p["ontogpt_ids"]) for p in per_patient)
    total_exact = sum(len(p["exact_overlap"]) for p in per_patient)
    total_ph_close = sum(len(p["phenoscribe_close"]) for p in per_patient)
    total_og_close = sum(len(p["ontogpt_close"]) for p in per_patient)
    total_ph_unique = sum(len(p["phenoscribe_unique"]) for p in per_patient)
    total_og_unique = sum(len(p["ontogpt_unique"]) for p in per_patient)

    n = len(per_patient)
    zero_count = sum(1 for p in per_patient if not p["ontogpt_ids"])
    ph_avg = total_ph / n if n else 0
    og_avg = total_og / n if n else 0
    ratio = total_ph / total_og if total_og else float("inf")
    overlap_pct_of_og = (total_exact / total_og * 100) if total_og else 0.0
    all_unknown = set().union(*(p["phenoscribe_unknown_to_hpotk"] for p in per_patient))

    lines = [
        "---",
        f"date: {date.today().isoformat()}",
        "type: benchmark",
        "---",
        "",
        "# ontoGPT vs Phenoscribe: benchmark",
        "",
        "## Summary",
        "",
        f"- Transcripts: {n}",
        f"- HPO codes per patient: Phenoscribe {ph_avg:.1f}, ontoGPT {og_avg:.1f} (ratio {ratio:.1f})",
        f"- ontoGPT codes that match a Phenoscribe code exactly: {overlap_pct_of_og:.0f}% ({total_exact} of {total_og})",
        f"- Codes unique to ontoGPT: {total_og_unique} of {total_og}",
        f"- Codes unique to Phenoscribe: {total_ph_unique} of {total_ph}",
        f"- Transcripts where ontoGPT returns no code: {zero_count} of {n}",
        "",
    ]
    if all_unknown:
        lines += [
            "### HPO release skew",
            "",
            f"{len(all_unknown)} of the {total_ph} Phenoscribe codes ({(len(all_unknown)/total_ph*100):.0f}%) are unknown to the hpo-toolkit ontology snapshot (e.g. `{sorted(all_unknown)[:5]}`). They count as \"unique\", because the IC similarity needs a code that the graph knows.",
            "",
        ]
    lines += [
        "## Setup notes",
        "",
        "Three install problems:",
        "",
        "1. **Pydantic schema rejects ontoGPT's own output.** `ontogpt extract -t human_phenotype` crashes on every input with `extra_forbidden` for `original_spans`. The `HumanPhenotypeSet` model sets `extra='forbid'` but the SPIRES engine emits the field. The fix is a patch of the generated template file (see the header of `scripts/dev/benchmark_ontogpt.py`).",
        "2. **Default install silently drops HPO grounding.** Without warming the oaklib `sqlite:obo:hp` adapter (lazy download), ontoGPT returns `AUTO:phrase%20text` IDs instead of `HP:codes` and ships them under `named_entities` with no warning.",
        "3. **English-only HPO synonyms.** French input produces `AUTO:` IDs even with HPO cached, because oaklib only matches English labels and synonyms. The script translates French → English with `phenoscribe.llm.llm_call` (gpt-4o) before handing the text to ontoGPT.",
        "",
        "## Method",
        "",
        "For each pseudonymised transcript in `output/pseudo/`:",
        "",
        "1. Translate French → English via gpt-4o using a clinical-faithful system prompt that preserves the pseudonymisation tokens.",
        "2. Run `ontogpt extract -t human_phenotype -m gpt-4o -O json` on the English text.",
        "3. Collect the set of grounded `HP:` IDs from `named_entities`.",
        "4. Compare against Phenoscribe's HP IDs for the same patient (from `output/results.xlsx`).",
        f"5. Classify each predicted code as **exact overlap**, **close** (Lin information-content similarity ≥ {CLOSE_TAU:g} to a code on the other side, via the MICA in `hpo-toolkit`), or **unique** to that tool.",
        "",
        "The report gives no ground-truth F1: it compares the two tools on the same input.",
        "",
        "## Per-patient",
        "",
        "| Patient | Phenoscribe codes | ontoGPT codes | Exact overlap | Phenoscribe close | ontoGPT close | Phenoscribe unique | ontoGPT unique |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in per_patient:
        lines.append(
            f"| {p['pid']} | {len(p['phenoscribe_ids'])} | {len(p['ontogpt_ids'])} | "
            f"{len(p['exact_overlap'])} | {len(p['phenoscribe_close'])} | {len(p['ontogpt_close'])} | "
            f"{len(p['phenoscribe_unique'])} | {len(p['ontogpt_unique'])} |"
        )
    lines += [
        f"| **total** | **{total_ph}** | **{total_og}** | **{total_exact}** | **{total_ph_close}** | **{total_og_close}** | **{total_ph_unique}** | **{total_og_unique}** |",
        "",
        "## Sample per-patient detail",
        "",
    ]
    for p in per_patient:
        lines.append(f"### {p['pid']}")
        lines.append("")
        lines.append(f"- Exact overlap ({len(p['exact_overlap'])}): {sorted(p['exact_overlap']) or '—'}")
        lines.append(f"- Phenoscribe only, no close ontoGPT match ({len(p['phenoscribe_unique'])}): {sorted(p['phenoscribe_unique']) or '—'}")
        lines.append(f"- ontoGPT only, no close Phenoscribe match ({len(p['ontogpt_unique'])}): {sorted(p['ontogpt_unique']) or '—'}")
        lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines))
    logger.info("wrote report to %s", REPORT_PATH)


if __name__ == "__main__":
    main()
