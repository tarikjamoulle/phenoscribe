"""HPO ontology parser and ChromaDB index."""

import functools
import logging
import re
import unicodedata
from pathlib import Path

import chromadb
import requests

from phenoscribe.embeddings import get_embedding_function

logger = logging.getLogger(__name__)


class HpoVersionMismatch(RuntimeError):
    """Raised when the on-disk obo data-version does not match the pinned release."""


class EmbeddingModelMismatch(RuntimeError):
    """Raised when the configured embedding_model differs from the one the index was seeded with.

    Querying a collection seeded with encoder A using vectors from encoder B is a
    geometry mismatch: the distances are meaningless and retrieval is garbage. We
    fail loud instead of returning silently wrong codes.
    """


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

    with open(path, encoding="utf-8") as f:
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


def obo_url(release: str) -> str:
    """GitHub download URL of the hp.obo for a pinned release.

    ``hp/releases/2026-02-16`` is published under the tag ``v2026-02-16``.
    """
    tag = "v" + release.rsplit("/", 1)[-1]
    return (
        "https://github.com/obophenotype/human-phenotype-ontology/releases/"
        f"download/{tag}/hp.obo"
    )


def download_obo(obo_path: str, release: str) -> None:
    """Download the pinned hp.obo to ``obo_path`` and verify its header.

    Writes to a temporary file first, so a broken download never leaves a
    half-written obo that a later run takes for a good one.
    """
    path = Path(obo_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with requests.get(obo_url(release), stream=True, timeout=60) as response:
        response.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in response.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    check_obo_version(str(tmp), release)
    tmp.replace(path)


def obo_ready(obo_path: str, release: str) -> bool:
    """True when the obo exists and carries the pinned release."""
    try:
        check_obo_version(obo_path, release)
    except (FileNotFoundError, ValueError, HpoVersionMismatch):
        return False
    return True


# The layout of the documents in the index. 2: one document for each term (name,
# synonyms and definition) plus one for each name and EXACT/NARROW synonym.
# A change here makes `phenoscribe setup` build the index again.
INDEX_SCHEMA = 2


def index_ready(chroma_path: str, release: str, embedding_model: str = "default") -> bool:
    """True when the ChromaDB index holds terms seeded from ``release`` with ``embedding_model``."""
    if not Path(chroma_path).exists():
        return False
    client = chromadb.PersistentClient(path=chroma_path)
    try:
        collection = client.get_collection("hpo_terms")
    except Exception:  # noqa: BLE001 — chromadb raises different types per version
        return False
    meta = collection.metadata or {}
    return (
        meta.get("hpo_release") == release
        and meta.get("embedding_model", "default") == embedding_model
        and meta.get("index_schema", 1) == INDEX_SCHEMA
        and collection.count() > 0
    )


def seed_pinned_index(
    obo_path: str, chroma_path: str, release: str, embedding_model: str = "default"
) -> int:
    """Guard the pinned release, then seed ChromaDB. Returns the number of terms.

    If the on-disk obo header does not match ``release`` this raises before
    any write, so a stale index is never written.
    """
    check_obo_version(obo_path, release)
    return seed_chromadb(obo_path, chroma_path, embedding_model=embedding_model)


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

    with open(obo_path, encoding="utf-8") as f:
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

    with open(obo_path, encoding="utf-8") as f:
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


def seed_chromadb(
    obo_path: str, chroma_path: str, embedding_model: str = "default"
) -> int:
    """Parse OBO and seed ChromaDB. Returns number of terms indexed.

    The obo release string is stored on the collection metadata so the index is
    self-describing and a later version guard can compare against it.

    embedding_model selects the embedding function: "default" uses ChromaDB's
    built-in ONNX model (all-MiniLM-L6-v2); "sapbert" uses the biomedical
    SapBERT model. The choice is stored on the collection so search uses the
    same function it was indexed with.
    """
    terms = parse_obo(obo_path)
    version = read_obo_version(obo_path)
    client = chromadb.PersistentClient(path=chroma_path)
    embedding_function = get_embedding_function(embedding_model)

    # Delete existing collection if present
    try:
        client.delete_collection("hpo_terms")
    except Exception:
        pass

    collection = client.get_or_create_collection(
        name="hpo_terms",
        metadata={
            "hnsw:space": "cosine",
            "hpo_release": version,
            "embedding_model": embedding_model,
            "index_schema": INDEX_SCHEMA,
        },
        embedding_function=embedding_function,
    )

    docs = list(_documents(terms))
    # Batch insert (ChromaDB max batch is 41666)
    batch_size = 5000
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        collection.add(
            ids=[d[0] for d in batch],
            documents=[d[1] for d in batch],
            metadatas=[d[2] for d in batch],
        )

    return len(terms)


def _documents(terms: list[dict]):
    """(id, text, metadata) for each document of the index.

    The first document of a term has the HPO id as its id and the enriched text.
    One more document holds each name and EXACT/NARROW synonym on its own, so a
    plain synonym ("shortness of breath") is not lost in the mix of the enriched
    text. Its id is "<HPO id>#<n>"; metadata "hpo_id" names the term.
    """
    for t in terms:
        meta = {
            "hpo_id": t["id"],
            "name": t["name"],
            "parents": ",".join(t["parents"]),
            "synonyms": "; ".join(
                s["text"] for s in t["synonyms"] if s["scope"] in EMBEDDED_SCOPES
            )[:500],
        }
        yield t["id"], build_enriched_text(t), meta
        texts = [t["name"]] + [s["text"] for s in t["synonyms"] if s["scope"] in EMBEDDED_SCOPES]
        seen = set()
        for text in texts:
            key = fold_name(text)
            if key in seen:
                continue
            seen.add(key)
            yield f"{t['id']}#{len(seen)}", text, meta


def fold_name(text: str) -> str:
    """A term name for an exact lookup: lower case, no accents, '-' and '_' as spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return " ".join(re.sub(r"[-_/]", " ", text).split()).strip(" .,;:")


@functools.lru_cache(maxsize=4)
def exact_name_map(obo_path: str) -> dict[str, tuple[tuple[str, str], ...]]:
    """Folded HPO name or EXACT synonym -> the (id, name) of each term that carries it.

    The vector index mixes a term's name, synonyms and definition in one vector,
    so a plain synonym can rank low ("shortness of breath" put Dyspnea at rank 15,
    2026-10-27). An exact lookup finds it first. A name that several terms carry
    keeps all their ids.
    """
    names: dict[str, list[tuple[str, str]]] = {}
    for term in parse_obo(obo_path):
        texts = [term["name"]] + [s["text"] for s in term["synonyms"] if s["scope"] == "EXACT"]
        for text in texts:
            entry = names.setdefault(fold_name(text), [])
            if (term["id"], term["name"]) not in entry:
                entry.append((term["id"], term["name"]))
    return {key: tuple(value) for key, value in names.items()}


def exact_hits(clinical_term: str, obo_path: str) -> tuple[tuple[str, str], ...]:
    """The HPO terms whose name or EXACT synonym is the clinical term ("fall" finds "Falls")."""
    names = exact_name_map(obo_path)
    key = fold_name(clinical_term)
    variants = [key, key + "s"]
    if key.endswith("s") and len(key) > 4:
        variants.append(key[:-1])
    for variant in variants:
        if variant in names:
            return names[variant]
    return ()


def search_hpo(
    clinical_term: str,
    k: int = 5,
    chroma_path: str = "data/chroma_db",
    embedding_model: str | None = None,
    obo_path: str | None = None,
) -> list[dict]:
    """Search HPO index for closest matches to a clinical term.

    With ``obo_path``, an exact name hit (see :func:`exact_hits`) comes first,
    marked ``exact=True`` (and ``ambiguous=True`` when the name belongs to more
    than one term). Its ``distance`` is the vector distance when the vector
    search also found it, else None. The vector hits follow, without repeats.

    embedding_model must match the one the collection was seeded with. If
    None, it is read back from the collection metadata, so callers normally
    do not need to pass it. If an explicit embedding_model is passed and it
    does not match the seeded collection, we raise EmbeddingModelMismatch
    rather than querying with vectors from the wrong encoder.
    """
    client = chromadb.PersistentClient(path=chroma_path)
    seeded_model = (client.get_collection("hpo_terms").metadata or {}).get(
        "embedding_model", "default"
    )
    if embedding_model is None:
        embedding_model = seeded_model
    elif embedding_model != seeded_model:
        raise EmbeddingModelMismatch(
            f"Embedding model mismatch: config asks for "
            f"hpo_index.embedding_model={embedding_model!r} but the ChromaDB "
            f"collection at {chroma_path!r} was seeded with {seeded_model!r}. "
            f"Querying a {seeded_model!r} index with {embedding_model!r} vectors "
            f"is a geometry mismatch and returns garbage retrieval. Re-seed the "
            f"index for {embedding_model!r} (e.g. "
            f"`python scripts/build_sapbert_index.py` for sapbert), or set "
            f"hpo_index.embedding_model back to {seeded_model!r} in config.yaml."
        )
    collection = client.get_collection(
        "hpo_terms",
        embedding_function=get_embedding_function(embedding_model),
    )

    # A term has several documents, so ask for more and keep the best one of
    # each term until there are k terms.
    total = collection.count()
    n = min(4 * k, total)
    while True:
        results = collection.query(query_texts=[clinical_term], n_results=n)
        matches, seen = [], set()
        for i, doc_id in enumerate(results["ids"][0]):
            meta = results["metadatas"][0][i]
            hpo_id = meta.get("hpo_id") or doc_id.split("#")[0]
            if hpo_id in seen:
                continue
            seen.add(hpo_id)
            matches.append(
                {
                    "hpo_id": hpo_id,
                    "name": meta["name"],
                    "distance": results["distances"][0][i] if results["distances"] else None,
                }
            )
        if len(matches) >= k or n >= total:
            break
        n = min(n * 2, total)
    matches = matches[:k]

    exact = exact_hits(clinical_term, obo_path) if obo_path else ()
    if exact:
        found = {m["hpo_id"]: m for m in matches}
        first = [
            {
                "hpo_id": hpo_id,
                "name": name,
                "distance": found[hpo_id]["distance"] if hpo_id in found else None,
                "exact": True,
                "ambiguous": len(exact) > 1,
            }
            for hpo_id, name in exact
        ]
        ids = {m["hpo_id"] for m in first}
        matches = (first + [m for m in matches if m["hpo_id"] not in ids])[: max(k, len(first))]

    return matches
