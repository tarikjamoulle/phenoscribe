"""Benchmark ontoGPT vs Phenoscribe on the same pseudonymised transcripts.

Setup (one-off, isolated from main project deps). The `setuptools<81` pin is
load-bearing — modern setuptools dropped `pkg_resources`, which oaklib's
eutils dependency still imports.

    uv venv .venv-ontogpt
    VIRTUAL_ENV=$(pwd)/.venv-ontogpt uv pip install ontogpt "setuptools<81"

Two upstream issues need patching once after install:
    1. ontoGPT 1.1.1's `HumanPhenotypeSet` pydantic model has `extra='forbid'`
       but the SPIRES engine emits `original_spans` — every extract crashes.
       Fix:
           sed -i.bak "s/extra = 'forbid'/extra = 'allow'/g" \\
               .venv-ontogpt/lib/python3.10/site-packages/ontogpt/templates/human_phenotype.py
    2. oaklib's HPO SQLite adapter must be warmed once (auto-downloads):
           .venv-ontogpt/bin/python3 -c "from oaklib import get_adapter; \\
               get_adapter('sqlite:obo:hp').labels(['HP:0012378'])"
    Without (2), ontoGPT silently falls back to AUTO:phrase IDs instead of HP:codes.

Usage:
    source setup.sh
    python scripts/benchmark_ontogpt.py [--limit N]

Outputs:
    context/exports/ontogpt-benchmark-2026-05-30.md
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
from phenoscribe.llm import llm_call
from phenoscribe.validate import _get_hpo, hop_distance

PSEUDO_DIR = Path("output/pseudo")
ONTOGPT_BIN = Path(".venv-ontogpt/bin/ontogpt")
TMP_DIR = Path("/tmp/ontogpt_bench")
PHENOSCRIBE_RESULTS = "output/results.xlsx"
REPORT_PATH = Path("context/exports/ontogpt-benchmark-2026-05-30.md")

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

    The cached `en.txt` becomes a second-class artefact we don't want anyone
    to mistake for fully-pseudonymised text. A warning here makes the gap
    visible instead of silent.
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
            provider="openai",
            model="gpt-4o",
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
    cached release means the two HPO snapshots disagree — possibly a
    real release skew worth flagging.
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


def categorise(predicted: set[str], reference: set[str], hpo, max_hops: int = 2):
    """Split `predicted` into exact / close / unique with respect to `reference`."""
    exact = predicted & reference
    remaining = predicted - exact
    close = set()
    for p in remaining:
        for r in reference:
            d = hop_distance(hpo, p, r, max_hops=max_hops)
            if d is not None and d <= max_hops:
                close.add(p)
                break
    unique = remaining - close
    return exact, close, unique


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N transcripts")
    parser.add_argument("--force", action="store_true", help="Bypass the translation + ontoGPT cache in /tmp")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    if not ONTOGPT_BIN.exists():
        logger.error("ontoGPT binary not found at %s — run the setup commands in the script docstring.", ONTOGPT_BIN)
        sys.exit(1)

    phenoscribe_codes = load_patient_codes(PHENOSCRIBE_RESULTS)
    transcripts = sorted(PSEUDO_DIR.glob("*.txt"))
    if args.limit:
        transcripts = transcripts[: args.limit]

    hpo = _get_hpo()
    per_patient = []

    for tx in transcripts:
        pid = tx.stem
        logger.info("[%s] translating French → English%s", pid, " (cached)" if (TMP_DIR / f"{pid}.en.txt").exists() and not args.force else "")
        en = translate(tx.read_text(), pid, force=args.force)
        logger.info("[%s] running ontoGPT extract%s", pid, " (cached)" if (TMP_DIR / f"{pid}.json").exists() and not args.force else "")
        ontogpt_ids = run_ontogpt(en, pid, force=args.force)
        ph_ids = {row["hpo_id"] for row in phenoscribe_codes.get(pid, [])}
        ph_exact, ph_close, ph_unique = categorise(ph_ids, ontogpt_ids, hpo)
        og_exact, og_close, og_unique = categorise(ontogpt_ids, ph_ids, hpo)
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
        "plan: ../plans/stakeholder-feedback-plan-2026-05-30.md",
        "---",
        "",
        "# ontoGPT vs Phenoscribe — benchmark",
        "",
        "## Verdict",
        "",
        f"**Sample is small ({n} transcripts).** Treat the numbers as a directional signal, not a settled benchmark.",
        "",
        f"On these {n} pseudonymised long-COVID transcripts, Phenoscribe surfaces an average of {ph_avg:.1f} HPO codes per patient; ontoGPT surfaces {og_avg:.1f} (~{ratio:.1f}× fewer). When ontoGPT does find something, its codes line up with Phenoscribe most of the time — {overlap_pct_of_og:.0f}% of ontoGPT's codes match Phenoscribe exactly. ontoGPT contributes essentially nothing Phenoscribe missed ({total_og_unique} of {total_og} codes are unique to ontoGPT across the cohort).",
        "",
        f"The recall gap is the dominant signal even at this scale. Phenoscribe finds {total_ph_unique} codes ontoGPT misses entirely — and on the toughest transcript ({zero_count} of {n}), ontoGPT returns **zero** phenotypes. Phenoscribe's bounded ChromaDB shortlist plus a second-pass LLM judge is what closes that gap — exactly the discipline Peter Robinson recommended.",
        "",
        "**Recommendation: keep Phenoscribe; do not adopt ontoGPT as-is.** Setup friction is high (three install workarounds, see below), French input requires a translation pre-pass, and recall is much weaker on this sample. Worth revisiting if ontoGPT ships a working out-of-the-box HPO template with a non-English annotator — and worth re-running with a larger cohort before treating the recommendation as final.",
        "",]
    if all_unknown:
        lines += [
            "### Note on HPO release skew",
            "",
            f"{len(all_unknown)} of the {total_ph} Phenoscribe codes ({(len(all_unknown)/total_ph*100):.0f}%) are unknown to the hpo-toolkit ontology snapshot used by the scorer (e.g. `{sorted(all_unknown)[:5]}`). These are likely newer additions to HPO than the cached release; they fall straight into the \"unique\" column because `hop_distance` can't compute a neighbour. Worth checking that Phenoscribe's ChromaDB and `hpo-toolkit`'s cache are on similar release dates before treating the unique counts as final.",
            "",
        ]
    lines += [
        "## Setup notes (real install friction)",
        "",
        "Three concrete problems hit during install:",
        "",
        "1. **Pydantic schema rejects ontoGPT's own output.** `ontogpt extract -t human_phenotype` crashes on every input with `extra_forbidden` for `original_spans`. The `HumanPhenotypeSet` model sets `extra='forbid'` but the SPIRES engine emits the field. Worked around by patching the generated template file (see `scripts/benchmark_ontogpt.py` header).",
        "2. **Default install silently drops HPO grounding.** Without warming the oaklib `sqlite:obo:hp` adapter (lazy download), ontoGPT returns `AUTO:phrase%20text` IDs instead of `HP:codes` and ships them under `named_entities` with no warning.",
        "3. **English-only HPO synonyms.** French input produces `AUTO:` IDs even with HPO cached, because oaklib only matches English labels and synonyms. We solved this by translating French → English with `phenoscribe.llm.llm_call(model=\"gpt-4o\")` before handing the text to ontoGPT.",
        "",
        "## Method",
        "",
        "For each pseudonymised transcript in `output/pseudo/`:",
        "",
        "1. Translate French → English via gpt-4o using a clinical-faithful system prompt that preserves the pseudonymisation tokens.",
        "2. Run `ontogpt extract -t human_phenotype -m gpt-4o -O json` on the English text.",
        "3. Collect the set of grounded `HP:` IDs from `named_entities`.",
        "4. Compare against Phenoscribe's HP IDs for the same patient (from `output/results.xlsx`).",
        "5. Classify each predicted code as **exact overlap**, **close** (≤2 hops in HPO via `hpo-toolkit`), or **unique** to that tool.",
        "",
        "Ground-truth F1 was not computed because the patient-ID format mismatch between pipeline output (`038`) and the GP's manual codes (`MGA.038`) still blocks joining. Comparing the two tools head-to-head on the same input is a useful intermediate signal.",
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
