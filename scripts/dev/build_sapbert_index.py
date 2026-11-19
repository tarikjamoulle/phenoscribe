"""Build a SapBERT-embedded HPO index in .tmp/chroma_sapbert, for recall tests.

It writes only to the gitignored .tmp/ folder and never changes data/chroma_db.
To use SapBERT in the pipeline, set hpo_index.embedding_model: sapbert in
config.yaml and run `phenoscribe setup`.

    uv run python scripts/dev/build_sapbert_index.py
"""

import sys
import time
from pathlib import Path

from phenoscribe.hpo_index import seed_chromadb

ROOT = Path(__file__).resolve().parents[2]
OBO = str(ROOT / "data" / "hpo" / "hp.obo")
OUT = str(ROOT / ".tmp" / "chroma_sapbert")


def main() -> None:
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n = seed_chromadb(OBO, OUT, embedding_model="sapbert")
    print(f"indexed {n} terms with sapbert in {time.time()-t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
