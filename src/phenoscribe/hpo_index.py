"""HPO ontology parser and ChromaDB index."""

import logging
import re
from pathlib import Path

import chromadb

logger = logging.getLogger(__name__)


class HpoVersionMismatch(RuntimeError):
    """Raised when the on-disk obo data-version does not match the pinned release."""


def read_obo_version(obo_path: str) -> str:
    """Return the ``data-version:`` header from an obo file (e.g. ``hp/releases/2026-02-16``).

    The header lives in the first few lines of every HPO release. We read only
    the head of the file so this stays cheap to call at startup.

    Raises:
        FileNotFoundError: the obo file is missing.
        ValueError: no ``data-version:`` header was found.
    """
    path = Path(obo_path)
    if not path.exists():
        raise FileNotFoundError(f"HPO obo not found: {obo_path}")

    with open(path) as f:
        for _ in range(50):
            line = f.readline()
            if not line:
                break
            if line.startswith("data-version:"):
                return line[len("data-version:"):].strip()
    raise ValueError(f"No 'data-version:' header found in {obo_path}")


def check_obo_version(obo_path: str, expected_release: str) -> str:
    """Verify the on-disk obo matches the pinned release. Returns the version on success.

    Raises:
        HpoVersionMismatch: the obo header does not equal ``expected_release``.
    """
    actual = read_obo_version(obo_path)
    if actual != expected_release:
        raise HpoVersionMismatch(
            f"HPO release mismatch: config pins {expected_release!r} but "
            f"{obo_path} is {actual!r}. The ontology on disk, the ChromaDB index "
            f"and config.yaml must all agree. Re-seed the index from the pinned "
            f"release, or update hpo.release to match the obo."
        )
    return actual


# OBO synonym scopes (OBO 1.4 spec). EXACT means same meaning as the term;
# NARROW is more specific than the term; BROAD is more general; RELATED is a
# loose association used in the literature but not strictly correct.
# An EXACT or NARROW synonym still denotes the term (or a subset of it), so it
# is safe to embed. A BROAD synonym denotes a wider concept and a RELATED one
# only an associated concept, so embedding them pulls the term's vector toward
# neighbouring concepts and dilutes the match.
# Spec: https://owlcollab.github.io/oboformat/doc/GO.format.obo-1_4.html
SYNONYM_SCOPES = ("EXACT", "NARROW", "BROAD", "RELATED")

# Scopes whose text we embed by default.
EMBEDDED_SCOPES = frozenset({"EXACT", "NARROW"})

# OBO syntax: synonym: "text" SCOPE [optional type] [dbxrefs]
# The scope token follows the closing quote. It is required in practice across
# the HPO release; if absent the OBO spec defaults it to RELATED.
_SYNONYM_RE = re.compile(r'^synonym: "(.+?)"(?:\s+(EXACT|NARROW|BROAD|RELATED))?')


def parse_obo(obo_path: str) -> list[dict]:
    """Parse HPO OBO file into a list of term dicts.

    Obsolete terms are dropped from the returned list (they should never be
    indexed or matched), but their ``replaced_by`` and ``consider`` targets are
    captured first so callers can resolve an obsolete id to its replacement.
    Merged ids (``alt_id``) are recorded the same way. Use
    :func:`build_obsolete_map` on the same file to get that mapping.
    """
    terms = []
    current: dict | None = None

    def flush(term: dict | None) -> None:
        if term and not term.get("is_obsolete"):
            terms.append(term)

    with open(obo_path) as f:
        for line in f:
            line = line.rstrip("\n")

            if line == "[Term]":
                flush(current)
                current = _new_term()
                continue

            if line == "[Typedef]":
                flush(current)
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
                match = _SYNONYM_RE.match(line)
                if match:
                    text = match.group(1)
                    # Default to RELATED per OBO spec when scope is missing.
                    scope = match.group(2) or "RELATED"
                    current["synonyms"].append({"text": text, "scope": scope})
            elif line.startswith("is_a: "):
                parent_id = line[6:].split("!")[0].strip()
                current["parents"].append(parent_id)
            elif line.startswith("alt_id: "):
                current["alt_ids"].append(line[8:].split("!")[0].strip())
            elif line.startswith("replaced_by: "):
                current["replaced_by"].append(line[13:].split("!")[0].strip())
            elif line.startswith("consider: "):
                current["consider"].append(line[10:].split("!")[0].strip())
            elif line.startswith("is_obsolete: true"):
                current["is_obsolete"] = True

    # Don't forget the last term
    flush(current)

    # Filter to only HP: terms with names
    return [t for t in terms if t["id"].startswith("HP:") and t["name"]]


def _new_term() -> dict:
    return {
        "id": "",
        "name": "",
        "definition": "",
        "synonyms": [],
        "parents": [],
        "alt_ids": [],
        "replaced_by": [],
        "consider": [],
        "is_obsolete": False,
    }


def build_obsolete_map(obo_path: str) -> dict[str, str]:
    """Map every retired HPO id to the active id that should be used instead.

    Covers two ways an id retires:

    * Obsolete terms with ``replaced_by:`` — the OBO format says this target is
      safe for automatic reassignment, so we use it directly.
    * Merged terms — the old id becomes an ``alt_id:`` of the surviving term,
      so it resolves to that term's primary id.

    Obsolete terms that only carry ``consider:`` (no ``replaced_by:``) are left
    out. The OBO spec marks ``consider`` as needing human review, so we don't
    rewrite those automatically. :func:`resolve_obsolete` exposes them
    separately for callers that want to surface a suggestion.
    """
    mapping: dict[str, str] = {}
    current: dict | None = None

    def flush(term: dict | None) -> None:
        if not term or not term["id"].startswith("HP:"):
            return
        if term["is_obsolete"] and term["replaced_by"]:
            mapping[term["id"]] = term["replaced_by"][0]
        for alt in term["alt_ids"]:
            # alt_id points at a still-live primary id; never overwrite a
            # replaced_by entry with an alt mapping for the same key.
            mapping.setdefault(alt, term["id"])

    with open(obo_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if line == "[Term]":
                flush(current)
                current = _new_term()
                continue
            if line == "[Typedef]":
                flush(current)
                current = None
                continue
            if current is None:
                continue
            if line.startswith("id: "):
                current["id"] = line[4:]
            elif line.startswith("alt_id: "):
                current["alt_ids"].append(line[8:].split("!")[0].strip())
            elif line.startswith("replaced_by: "):
                current["replaced_by"].append(line[13:].split("!")[0].strip())
            elif line.startswith("is_obsolete: true"):
                current["is_obsolete"] = True
    flush(current)
    return mapping


def resolve_obsolete(hpo_id: str, obsolete_map: dict[str, str]) -> str:
    """Resolve a possibly-retired HPO id to its active id.

    Returns the active id if ``hpo_id`` was obsoleted-with-replacement or merged
    (via :func:`build_obsolete_map`); otherwise returns ``hpo_id`` unchanged.
    Follows a short chain in case a replacement was itself later retired.
    """
    seen = set()
    current = hpo_id
    while current in obsolete_map and current not in seen:
        seen.add(current)
        current = obsolete_map[current]
    return current


def build_enriched_text(
    term: dict, embedded_scopes: frozenset[str] = EMBEDDED_SCOPES
) -> str:
    """Build enriched text for embedding: name + in-scope synonyms + definition.

    Only synonyms whose scope is in ``embedded_scopes`` are included. The
    default keeps EXACT and NARROW and drops BROAD and RELATED, which would
    otherwise pull the term's embedding toward broader or merely-associated
    concepts. Pass a wider set (e.g. all four scopes) to restore the old
    behaviour.
    """
    parts = [term["name"]]
    syn_texts = [
        s["text"] for s in term["synonyms"] if s["scope"] in embedded_scopes
    ]
    if syn_texts:
        parts.append("Synonyms: " + ", ".join(syn_texts))
    if term["definition"]:
        parts.append("Definition: " + term["definition"])
    return ". ".join(parts)


def build_hierarchy(terms: list[dict]) -> dict[str, list[str]]:
    """Build HPO hierarchy graph: id -> list of parent ids."""
    return {t["id"]: t["parents"] for t in terms}


def seed_chromadb(obo_path: str, chroma_path: str) -> int:
    """Parse OBO and seed ChromaDB. Returns number of terms indexed.

    The obo release string is stored on the collection metadata so the index is
    self-describing and a later version guard can compare against it.
    """
    terms = parse_obo(obo_path)
    version = read_obo_version(obo_path)
    client = chromadb.PersistentClient(path=chroma_path)

    # Delete existing collection if present
    try:
        client.delete_collection("hpo_terms")
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name="hpo_terms",
        metadata={"hnsw:space": "cosine", "hpo_release": version},
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
                    "synonyms": "; ".join(
                        s["text"]
                        for s in t["synonyms"]
                        if s["scope"] in EMBEDDED_SCOPES
                    )[:500],
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
