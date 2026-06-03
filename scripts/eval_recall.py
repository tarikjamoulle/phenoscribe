"""Measure recall@5 and recall@10 for the HPO vector index.

No LLM. For each ground-truth (clinical label -> HP code) pair, query the
index with the label and check whether the gold code is in the top-k results.
Run it for the shared default index and the SapBERT index, side by side.

The top-k shortlist is the ceiling on the whole pipeline: if the gold code is
not retrieved, the downstream LLM judge can never pick it. That is exactly the
ceiling Robinson asked us to measure (issue #8, Robinson Test Q4).

Two recalls, reported side by side
----------------------------------
- **Per-mention** (micro): every (label, code) pair counts once. A common
  symptom with many synonymous labels contributes many pairs, so frequent
  concepts dominate the average. This is the "how often does a query string
  retrieve its code" number.
- **Per-concept** (macro): each unique gold HP code counts once. For a code
  with several labels, it is a hit at k iff ANY of its labels retrieves it at
  k. This weighs a 50-synonym symptom and a 1-synonym rare finding equally —
  "what fraction of distinct concepts can the index reach at all".

They diverge materially on this label set, which is Robinson's "per-mention
recall wearing a per-concept label" objection. Both are printed so neither is
read as the other.

Usage:
    PYTHONPATH=src python scripts/eval_recall.py
"""

import sys
from collections import OrderedDict
from pathlib import Path

import pandas as pd

from phenoscribe.hpo_index import search_hpo

WT = Path(__file__).resolve().parents[1]
MAIN = Path("/Users/tarikjamoulle/projects/hpo_identifier")
GT_CSV = MAIN / "data" / "ground_truth" / "hop_list_terms.csv"
DEFAULT_INDEX = str(MAIN / "data" / "chroma_db")
SAPBERT_INDEX = str(WT / ".tmp" / "chroma_sapbert")
KS = (5, 10)


def load_gold_pairs() -> list[tuple[str, str]]:
    """Unique (label, HP code) pairs from the manual ground truth.

    The manual codes are Marc Jamoulle's work. We dedupe identical pairs so a
    common symptom is not counted many times, and keep label casing as given.
    """
    df = pd.read_csv(GT_CSV)
    pairs: "OrderedDict[tuple[str, str], None]" = OrderedDict()
    for _, row in df.iterrows():
        label = str(row["observation_source_value"]).strip()
        code = str(row["HPO_code"]).strip()
        if not label or label == "nan":
            continue
        if not code.startswith("HP:"):
            continue
        pairs[(label, code)] = None
    return list(pairs.keys())


def codes_present(codes: set[str], chroma_path: str) -> set[str]:
    """Which gold codes actually exist as ids in this index."""
    import chromadb

    client = chromadb.PersistentClient(path=chroma_path)
    col = client.get_collection("hpo_terms")
    found: set[str] = set()
    code_list = list(codes)
    for i in range(0, len(code_list), 200):
        chunk = code_list[i : i + 200]
        got = col.get(ids=chunk)
        found.update(got["ids"])
    return found


def score_recall(pairs, present: set[str], rank_fn, ks=KS) -> dict:
    """Compute per-mention (micro) and per-concept (macro) recall@k.

    `rank_fn(label) -> list[str]` returns the ranked HP ids for a query label.
    Only pairs whose gold code is in `present` are scored, so a code that is not
    in the index is never silently counted as a retrieval miss.

    Per-mention: each (label, code) pair is a hit at k iff the code is in the
    top-k for that label. Recall = hits / pairs.

    Per-concept: each unique code is a hit at k iff ANY of its labels retrieves
    it in the top-k. Recall = concept hits / unique concepts. This is a macro
    average over concepts; concepts with many synonyms no longer dominate.
    """
    scorable = [(lbl, code) for (lbl, code) in pairs if code in present]
    mention_hits = {k: 0 for k in ks}
    # code -> {k: bool} hit-by-any-label
    concept_hit = {code: {k: False for k in ks} for _, code in scorable}
    for label, code in scorable:
        ranked = rank_fn(label)
        for k in ks:
            if code in ranked[:k]:
                mention_hits[k] += 1
                concept_hit[code][k] = True
    n_mentions = len(scorable)
    n_concepts = len(concept_hit)
    out = {"n_mentions": n_mentions, "n_concepts": n_concepts}
    for k in ks:
        out[f"per_mention_recall@{k}"] = (
            mention_hits[k] / n_mentions if n_mentions else 0.0
        )
        concept_hits_k = sum(1 for c in concept_hit if concept_hit[c][k])
        out[f"per_concept_recall@{k}"] = (
            concept_hits_k / n_concepts if n_concepts else 0.0
        )
    return out


def eval_index(name: str, chroma_path: str, pairs, present: set[str]) -> dict:
    max_k = max(KS)

    def rank_fn(label: str) -> list[str]:
        return [r["hpo_id"] for r in search_hpo(label, k=max_k, chroma_path=chroma_path)]

    return {"name": name, **score_recall(pairs, present, rank_fn)}


def main() -> None:
    pairs = load_gold_pairs()
    gold_codes = {code for _, code in pairs}
    print(f"ground-truth rows (unique label+code pairs): {len(pairs)}")
    print(f"unique gold HP codes: {len(gold_codes)}")

    present_default = codes_present(gold_codes, DEFAULT_INDEX)
    print(
        f"gold codes present in default index: {len(present_default)}/{len(gold_codes)}"
        f" (missing {len(gold_codes) - len(present_default)} obsolete/renamed in this release)"
    )

    if not Path(SAPBERT_INDEX).exists():
        print(f"SapBERT index not found at {SAPBERT_INDEX}; run build_sapbert_index.py first")
        sys.exit(1)
    present_sapbert = codes_present(gold_codes, SAPBERT_INDEX)

    # Score both on the SAME set of pairs (codes present in BOTH indexes) for a
    # fair head-to-head.
    common = present_default & present_sapbert
    print(f"scoring both on the {len(common)} codes present in both indexes\n")

    rows = [
        eval_index("default (all-MiniLM-L6-v2, 384d)", DEFAULT_INDEX, pairs, common),
        eval_index("sapbert (PubMedBERT-fulltext, 768d)", SAPBERT_INDEX, pairs, common),
    ]

    print("Per-mention (micro: every label+code pair counts once)")
    hdr = f"{'index':<40} {'mentions':>9} {'recall@5':>10} {'recall@10':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['name']:<40} {r['n_mentions']:>9} "
            f"{r['per_mention_recall@5']:>10.3f} {r['per_mention_recall@10']:>10.3f}"
        )

    print("\nPer-concept (macro: each unique HP code counts once; hit if ANY label finds it)")
    hdr = f"{'index':<40} {'concepts':>9} {'recall@5':>10} {'recall@10':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['name']:<40} {r['n_concepts']:>9} "
            f"{r['per_concept_recall@5']:>10.3f} {r['per_concept_recall@10']:>10.3f}"
        )


if __name__ == "__main__":
    main()
