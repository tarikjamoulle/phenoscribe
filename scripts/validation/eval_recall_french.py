"""French-path retrieval recall@5/@10 (the flag that was 'unknown').

The earlier output/validation/recall_at5.md could only *bracket* the French-path
ceiling because the extractor's `clinical_term` was never persisted: the run kept
only the French `Patient Verbatim` and the final `HPO Code`. This script closes
that gap by re-running LLM Call 1 (`extract_symptoms`) on each pseudonymized
French transcript, which gives us the missing English `clinical_term` per finding.

For each extracted (clinical_term, verbatim) we then:
  1. Recover the code the *pipeline* assigned to that finding, by joining to
     output/results.xlsx (Sheet) on (Patient_ID, verbatim). The assigned code is
     the pipeline's, not an oracle / Marc label.
  2. Query Chroma (data/chroma_db) with the fresh `clinical_term` via the same
     `search_hpo` the pipeline uses, top-10.
  3. Record whether the assigned code is in the top-5 (and top-10).

recall@k = (assigned code is in the clinical_term's top-k) / (matched mentions).

This is the FRENCH extractor path, NOT English-oracle labels.

Caveat on tautology: the assigned code came from a *previous* extraction run's
clinical_term, which is a different stochastic draw than this run's. So this is
not a pure replay of the runtime top-5; it measures whether *a* good-faith
French extraction of the same verbatim retrieves the code the pipeline landed
on. Where this run's clinical_term equals the run's clinical_term, the @5 number
is tautological by construction (judge could only pick from that top-5); where
they differ (paraphrase, stochasticity), it is a real retrieval test. We can't
separate the two because the run's clinical_term was not persisted.
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

from phenoscribe.extract_symptoms import extract_symptoms
from phenoscribe.hpo_index import search_hpo

ROOT = Path("/Users/tarikjamoulle/projects/hpo_identifier")
PSEUDO_DIR = ROOT / "output" / "pseudo_round3"  # fixed-PII transcripts (round-3); was output/pseudo (leaky)
RESULTS_XLSX = ROOT / "output" / "results.xlsx"
CHROMA = str(ROOT / "data" / "chroma_db")
EXTRACT_CACHE = ROOT / "output" / "validation" / "_french_extractions.json"
KS = (5, 10)
MAX_K = max(KS)


def norm(s: str) -> str:
    """Normalize a verbatim for fuzzy join: lowercase, strip punctuation/space."""
    s = (s or "").lower()
    s = re.sub(r"[^a-zà-ÿ0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_assigned(patient_id: str, results: pd.DataFrame) -> list[dict]:
    """Present, HP-coded rows for this patient: verbatim -> assigned code."""
    sub = results[
        (results["Patient_ID"] == patient_id)
        & (results["Present/Absent"] == "Present")
        & results["HPO Code"].notna()
    ]
    rows = []
    for _, r in sub.iterrows():
        rows.append(
            {
                "verbatim": str(r["Patient Verbatim"]),
                "code": str(r["HPO Code"]).strip(),
                "term": str(r["HPO Term"]),
            }
        )
    return rows


def best_match(verbatim: str, assigned: list[dict]) -> dict | None:
    """Find the assigned row whose verbatim best matches the extracted one.

    Exact-normalized first; then substring (either direction) on the longer of
    the two; require a reasonable overlap to avoid spurious joins.
    """
    nv = norm(verbatim)
    if not nv:
        return None
    # exact normalized
    for a in assigned:
        if norm(a["verbatim"]) == nv:
            return a
    # substring containment (handles the extractor splitting/merging a quote)
    cands = []
    for a in assigned:
        na = norm(a["verbatim"])
        if not na:
            continue
        if nv in na or na in nv:
            overlap = min(len(nv), len(na)) / max(len(nv), len(na))
            cands.append((overlap, a))
    if cands:
        cands.sort(key=lambda x: -x[0])
        if cands[0][0] >= 0.4:
            return cands[0][1]
    return None


def get_extractions() -> dict[str, list[dict]]:
    """One LLM extraction call per MGA file, cached to disk to avoid re-spend."""
    if EXTRACT_CACHE.exists():
        cached = json.loads(EXTRACT_CACHE.read_text())
        print(f"[cache] loaded extractions for {len(cached)} files", file=sys.stderr)
        return cached
    out = {}
    files = sorted(PSEUDO_DIR.glob("MGA.*.txt"))
    for fp in files:
        pid = fp.stem  # MGA.038
        text = fp.read_text()
        syms = extract_symptoms(text, provider="openai", model="gpt-4o")
        out[pid] = syms
        print(f"[extract] {pid}: {len(syms)} findings", file=sys.stderr)
    EXTRACT_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    return out


def main() -> None:
    results = pd.read_excel(RESULTS_XLSX, sheet_name="Sheet")
    extractions = get_extractions()

    rank_cache: dict[str, list[str]] = {}

    def ranked(term: str) -> list[str]:
        if term not in rank_cache:
            rank_cache[term] = [
                r["hpo_id"] for r in search_hpo(term, k=MAX_K, chroma_path=CHROMA)
            ]
        return rank_cache[term]

    per_patient = {}
    rows = []
    unmatched = 0
    total_present_findings = 0

    for pid in sorted(extractions):
        assigned = load_assigned(pid, results)
        # only present findings from the extraction are codeable
        present = [s for s in extractions[pid] if not s.get("negated", False)]
        total_present_findings += len(present)
        p_hits = {k: 0 for k in KS}
        p_n = 0
        for s in present:
            term = s["clinical_term"]
            a = best_match(s.get("patient_verbatim", ""), assigned)
            if a is None:
                unmatched += 1
                continue
            code = a["code"]
            rk = ranked(term)
            hit = {k: code in rk[:k] for k in KS}
            for k in KS:
                if hit[k]:
                    p_hits[k] += 1
            p_n += 1
            rows.append(
                {
                    "patient": pid,
                    "clinical_term": term,
                    "verbatim": s.get("patient_verbatim", ""),
                    "assigned_code": code,
                    "assigned_term": a["term"],
                    **{f"hit@{k}": hit[k] for k in KS},
                    "rank": (rk.index(code) + 1) if code in rk else None,
                }
            )
        per_patient[pid] = {"n": p_n, **{f"hits@{k}": p_hits[k] for k in KS}}

    n = sum(p["n"] for p in per_patient.values())
    agg = {}
    for k in KS:
        h = sum(p[f"hits@{k}"] for p in per_patient.values())
        agg[k] = (h, h / n if n else 0.0)

    out = {
        "n_mentions_matched": n,
        "n_present_findings_extracted": total_present_findings,
        "unmatched_to_pipeline_code": unmatched,
        "n_patients": len(per_patient),
        "recall": {str(k): {"hits": agg[k][0], "recall": agg[k][1]} for k in KS},
        "per_patient": per_patient,
        "rows": rows,
    }
    Path(ROOT / "output" / "validation" / "_french_recall_detail.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1)
    )
    # stdout summary
    print(json.dumps({k: out[k] for k in (
        "n_mentions_matched", "n_present_findings_extracted",
        "unmatched_to_pipeline_code", "n_patients", "recall")}, indent=2))
    print("\nPER PATIENT (n, hits@5, recall@5):")
    for pid in sorted(per_patient):
        p = per_patient[pid]
        r5 = p["hits@5"] / p["n"] if p["n"] else float("nan")
        print(f"  {pid}: n={p['n']:>3} hits@5={p['hits@5']:>3} recall@5={r5:.3f}")


if __name__ == "__main__":
    main()
