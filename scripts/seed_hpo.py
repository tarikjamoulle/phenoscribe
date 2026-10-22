"""Seed the HPO ontology into ChromaDB.

Guards the pinned release before seeding: if the on-disk obo header does not
match hpo.release in config.yaml the build/run fails here, so a stale index is
never written. `phenoscribe setup` runs the same code and also downloads the obo.
"""

from phenoscribe.config import load_config
from phenoscribe.hpo_index import seed_pinned_index

if __name__ == "__main__":
    config = load_config()
    embedding_model = config.hpo_index.embedding_model
    print(f"Seeding HPO {config.hpo.release} into ChromaDB (embedding: {embedding_model})...")
    count = seed_pinned_index(
        config.paths.hpo_obo,
        config.paths.chroma_db,
        config.hpo.release,
        embedding_model=embedding_model,
    )
    print(f"Indexed {count} HPO terms.")
