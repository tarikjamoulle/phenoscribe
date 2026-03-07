"""Seed the HPO ontology into ChromaDB."""

from phenoscribe.hpo_index import seed_chromadb

if __name__ == "__main__":
    print("Seeding HPO ontology into ChromaDB...")
    count = seed_chromadb("data/hpo/hp.obo", "data/chroma_db")
    print(f"Indexed {count} HPO terms.")
