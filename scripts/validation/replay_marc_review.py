"""Replay Marc's 8 flagged rows through the live matcher (search + LLM judge).

Each row in tests/fixtures/marc_review_2026-10.json is fed to match_hpo with
its original clinical_term, verbatim and negation. A row agrees when the code
the matcher picks is in the row's `accept` list (None = left uncoded).

This measures agreement with Marc's reading, not correctness: his codes are a
reviewer's reading, and two of them name other HPO terms (see `why`).

Needs an LLM key for the configured provider. Run:
    uv run python scripts/validation/replay_marc_review.py [--config config.yaml]
"""

import argparse
import json
import sys
from pathlib import Path

from phenoscribe.config import load_config
from phenoscribe.match_hpo import match_hpo

FIXTURE = Path("tests/fixtures/marc_review_2026-10.json")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    config = load_config(args.config)
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]

    agreed = 0
    for row in rows:
        symptom = {
            "clinical_term": row["clinical_term"],
            "patient_verbatim": row["patient_verbatim"],
            "negated": row["negated"],
        }
        out = match_hpo(
            [symptom],
            provider=config.llm.provider,
            model=config.llm.model,
            ollama_base_url=config.llm.ollama_base_url,
            chroma_path=config.paths.chroma_db,
            obo_path=config.paths.hpo_obo,
            embedding_model=config.hpo_index.embedding_model,
        )
        got = out[0]["hpo_id"] if out else None
        term = out[0]["hpo_term"] if out else "(no candidates)"
        ok = got in row["accept"]
        agreed += ok
        print(
            f"{'OK  ' if ok else 'DIFF'} {row['patient_id']}  {row['clinical_term']!r:28} "
            f"was {row['tool_code'] or '-':11} now {got or '-':11} {term}"
        )

    print(f"\n{agreed}/{len(rows)} rows agree with Marc's reading.")
    return 0 if agreed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
