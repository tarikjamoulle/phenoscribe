"""Seed the HPO ontology into ChromaDB.

Guards the pinned release before seeding: if the on-disk obo header does not
match hpo.release in config.yaml the build/run fails here, so a stale index is
never written.
"""

from phenoscribe.config import load_config
from phenoscribe.hpo_index import check_obo_version, seed_chromadb

if __name__ == "__main__":
    config = load_config()
    obo_path = config.paths.hpo_obo
    chroma_path = config.paths.chroma_db

    version = check_obo_version(obo_path, config.hpo.release)
    print(f"HPO release verified: {version}")

    print("Seeding HPO ontology into ChromaDB...")
    count = seed_chromadb(obo_path, chroma_path)
    print(f"Indexed {count} HPO terms.")
