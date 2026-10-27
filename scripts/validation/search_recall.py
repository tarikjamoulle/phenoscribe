"""Search recall on the reviewed clinical terms of 2026-10-27.

For each clinical term in tests/fixtures/review_codes_2026-10-27.json, find
the rank of the expected HPO code in the search shortlist. A combined finding
("nausea and diarrhea") counts as found when any expected code is in the list.

  review  - terms the matcher left uncoded; the reviewed code is the target.
  control - terms the matcher coded with no review flag; a fix must not lose them.

The targets come from one review (no gold standard), so the numbers measure
agreement with that review. Run:

    python scripts/validation/search_recall.py [--k 10] [--no-exact]
"""

import argparse
import json
from pathlib import Path

from phenoscribe.config import load_config
from phenoscribe.hpo_index import search_hpo

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "review_codes_2026-10-27.json"


def rank(case: dict, k: int, chroma: str, obo: str | None) -> int | None:
    hits = [h["hpo_id"] for h in search_hpo(case["clinical_term"], k=k, chroma_path=chroma, obo_path=obo)]
    found = [hits.index(code) + 1 for code in case["expected"] if code in hits]
    return min(found) if found else None


def report(name: str, cases: list[dict], k: int, chroma: str, obo: str | None) -> None:
    ranks = [rank(c, k, chroma, obo) for c in cases]
    rows = [c.get("rows", 1) for c in cases]
    def share(limit):
        hit = sum(n for r, n in zip(ranks, rows) if r and r <= limit)
        return f"{hit}/{sum(rows)} ({100 * hit / sum(rows):.0f}%)"
    print(f"{name:8s} recall@5 {share(5):>14s}   recall@{k} {share(k):>14s}")
    for c, r in zip(cases, ranks):
        if not r or r > 5:
            print(f"    miss@5  {c['clinical_term']!r} -> {c['expected']} (rank {r})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--no-exact", action="store_true", help="vector search only (the old behaviour)")
    parser.add_argument("--chroma", help="another index folder (default: config.yaml)")
    args = parser.parse_args()
    config = load_config("config.yaml")
    obo = None if args.no_exact else config.paths.hpo_obo
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name in ("review", "control"):
        report(name, fixture[name], args.k, args.chroma or config.paths.chroma_db, obo)


if __name__ == "__main__":
    main()
