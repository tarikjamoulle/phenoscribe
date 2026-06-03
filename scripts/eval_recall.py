"""Measure recall@5 and recall@10 for the HPO vector index.

No LLM. For each ground-truth (clinical label -> HP code) pair, query the
index with the label and check whether the gold code is in the top-k results.
Run it for the shared default index and the SapBERT index, side by side.

The top-k shortlist is the ceiling on the whole pipeline: if the gold code is
not retrieved, the downstream LLM judge can never pick it. That is exactly the
ceiling Robinson asked us to measure (issue #8, Robinson Test Q4).

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


def eval_index(name: str, chroma_path: str, pairs, present: set[str]) -> dict:
    max_k = max(KS)
    hits = {k: 0 for k in KS}
    # Only score pairs whose gold code is actually in the index, so a missing
    # code is not silently counted as a retrieval miss.
    scorable = [(lbl, code) for (lbl, code) in pairs if code in present]
    for label, code in scorable:
        results = search_hpo(label, k=max_k, chroma_path=chroma_path)
        ranked = [r["hpo_id"] for r in results]
        for k in KS:
            if code in ranked[:k]:
                hits[k] += 1
    n = len(scorable)
    return {
        "name": name,
        "n": n,
        **{f"recall@{k}": hits[k] / n if n else 0.0 for k in KS},
    }


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

    hdr = f"{'index':<40} {'n':>5} {'recall@5':>10} {'recall@10':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['name']:<40} {r['n']:>5} {r['recall@5']:>10.3f} {r['recall@10']:>10.3f}"
        )


if __name__ == "__main__":
    main()
