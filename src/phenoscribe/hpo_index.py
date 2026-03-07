"""HPO ontology parser and ChromaDB index."""

import re
from pathlib import Path

import chromadb


def parse_obo(obo_path: str) -> list[dict]:
    """Parse HPO OBO file into a list of term dicts."""
    terms = []
    current: dict | None = None

    with open(obo_path) as f:
        for line in f:
            line = line.rstrip("\n")

            if line == "[Term]":
                if current and not current.get("is_obsolete"):
                    terms.append(current)
                current = {
                    "id": "",
                    "name": "",
                    "definition": "",
                    "synonyms": [],
                    "parents": [],
                    "is_obsolete": False,
                }
                continue

            if line == "[Typedef]":
                if current and not current.get("is_obsolete"):
                    terms.append(current)
                current = None
                continue

            if current is None:
                continue

            if line.startswith("id: "):
                current["id"] = line[4:]
            elif line.startswith("name: "):
                current["name"] = line[6:]
            elif line.startswith("def: "):
                match = re.match(r'^def: "(.+?)"', line)
                if match:
                    current["definition"] = match.group(1)
            elif line.startswith("synonym: "):
                match = re.match(r'^synonym: "(.+?)"', line)
                if match:
                    current["synonyms"].append(match.group(1))
            elif line.startswith("is_a: "):
                parent_id = line[6:].split("!")[0].strip()
                current["parents"].append(parent_id)
            elif line.startswith("is_obsolete: true"):
                current["is_obsolete"] = True

    # Don't forget the last term
    if current and not current.get("is_obsolete"):
        terms.append(current)

    # Filter to only HP: terms with names
    return [t for t in terms if t["id"].startswith("HP:") and t["name"]]


def build_enriched_text(term: dict) -> str:
    """Build enriched text for embedding: name + synonyms + definition."""
    parts = [term["name"]]
    if term["synonyms"]:
        parts.append("Synonyms: " + ", ".join(term["synonyms"]))
    if term["definition"]:
        parts.append("Definition: " + term["definition"])
    return ". ".join(parts)


def build_hierarchy(terms: list[dict]) -> dict[str, list[str]]:
    """Build HPO hierarchy graph: id -> list of parent ids."""
    return {t["id"]: t["parents"] for t in terms}


def seed_chromadb(obo_path: str, chroma_path: str) -> int:
    """Parse OBO and seed ChromaDB. Returns number of terms indexed."""
    terms = parse_obo(obo_path)
    client = chromadb.PersistentClient(path=chroma_path)

    # Delete existing collection if present
    try:
        client.delete_collection("hpo_terms")
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name="hpo_terms",
        metadata={"hnsw:space": "cosine"},
    )

    # Batch insert (ChromaDB max batch is 41666)
    batch_size = 5000
    for i in range(0, len(terms), batch_size):
        batch = terms[i : i + batch_size]
        collection.add(
            ids=[t["id"] for t in batch],
            documents=[build_enriched_text(t) for t in batch],
            metadatas=[
                {
                    "name": t["name"],
                    "parents": ",".join(t["parents"]),
                    "synonyms": "; ".join(t["synonyms"][:10]),
                }
                for t in batch
            ],
        )

    return len(terms)


def search_hpo(
    clinical_term: str, k: int = 5, chroma_path: str = "data/chroma_db"
) -> list[dict]:
    """Search HPO index for closest matches to a clinical term."""
    client = chromadb.PersistentClient(path=chroma_path)
    collection = client.get_collection("hpo_terms")

    results = collection.query(query_texts=[clinical_term], n_results=k)

    matches = []
    for i in range(len(results["ids"][0])):
        matches.append(
            {
                "hpo_id": results["ids"][0][i],
                "name": results["metadatas"][0][i]["name"],
                "distance": results["distances"][0][i] if results["distances"] else None,
            }
        )

    return matches
