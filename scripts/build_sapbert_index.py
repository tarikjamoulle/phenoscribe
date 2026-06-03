"""Build a SapBERT-embedded HPO index under .tmp/chroma for recall eval.

Writes only to the gitignored scratch dir. Never touches the shared index.
"""

import sys
import time
from pathlib import Path

from phenoscribe.hpo_index import seed_chromadb

WT = Path(__file__).resolve().parents[1]
OBO = "/Users/tarikjamoulle/projects/hpo_identifier/data/hpo/hp.obo"
OUT = str(WT / ".tmp" / "chroma_sapbert")


def main() -> None:
    Path(OUT).parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    n = seed_chromadb(OBO, OUT, embedding_model="sapbert")
    print(f"indexed {n} terms with sapbert in {time.time()-t0:.0f}s -> {OUT}")


if __name__ == "__main__":
    sys.exit(main())
