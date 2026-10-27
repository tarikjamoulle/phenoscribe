"""Guard against querying a seeded ChromaDB index with the wrong encoder.

Robinson round-2 issue #8: the shipped collection is all-MiniLM
(embedding_model='default'), but config allows hpo_index.embedding_model:
sapbert. Without re-seeding, that path queries a MiniLM index with SapBERT
vectors — a geometry mismatch that silently returns garbage retrieval. The
guard in search_hpo fails loud instead, instructing the user to re-seed.
"""

import pytest

from phenoscribe.hpo_index import EmbeddingModelMismatch, search_hpo, seed_chromadb

MINI_OBO = (
    "format-version: 1.2\n"
    "data-version: hp/releases/2026-02-16\n\n"
    "[Term]\n"
    "id: HP:0001945\n"
    "name: Fever\n"
    'synonym: "Pyrexia" EXACT []\n\n'
    "[Term]\n"
    "id: HP:0002315\n"
    "name: Headache\n"
    'synonym: "Cephalgia" EXACT []\n'
)


def _seed_default(tmp_path):
    """Seed a tiny default (all-MiniLM) index and return its chroma path."""
    obo = tmp_path / "mini.obo"
    obo.write_text(MINI_OBO)
    chroma_path = str(tmp_path / "chroma")
    seed_chromadb(str(obo), chroma_path, embedding_model="default")
    return chroma_path


def test_configured_model_mismatch_raises_with_reseed_message(tmp_path):
    chroma_path = _seed_default(tmp_path)

    with pytest.raises(EmbeddingModelMismatch) as exc:
        search_hpo("fievre", k=2, chroma_path=chroma_path, embedding_model="sapbert")

    msg = str(exc.value)
    # Names both models so the user can see what they asked for vs what is on disk.
    assert "sapbert" in msg
    assert "default" in msg
    # Points at the concrete re-seed step, not a vague "fix your config".
    assert "scripts/build_sapbert_index.py" in msg


def test_matching_model_does_not_raise(tmp_path):
    chroma_path = _seed_default(tmp_path)

    # Configured model equals the seeded model: no guard, real retrieval runs.
    results = search_hpo(
        "fever", k=2, chroma_path=chroma_path, embedding_model="default"
    )
    assert results
    assert all("hpo_id" in r for r in results)


def test_none_reads_seeded_model_and_does_not_raise(tmp_path):
    chroma_path = _seed_default(tmp_path)

    # When the caller passes None, the seeded model is read back: never a mismatch.
    results = search_hpo("headache", k=2, chroma_path=chroma_path, embedding_model=None)
    assert results


# --- exact name lookup and one document for each synonym (2026-10-27) --------

EXACT_OBO = MINI_OBO + (
    "\n[Term]\n"
    "id: HP:0002094\n"
    "name: Dyspnea\n"
    'synonym: "Shortness of breath" EXACT layperson []\n'
    'synonym: "Breathlessness" NARROW []\n'
    "\n[Term]\n"
    "id: HP:0002527\n"
    "name: Falls\n"
    "\n[Term]\n"
    "id: HP:9999901\n"
    "name: Twin A\n"
    'synonym: "Same name" EXACT []\n'
    "\n[Term]\n"
    "id: HP:9999902\n"
    "name: Twin B\n"
    'synonym: "Same-name" EXACT []\n'
)


def _seed_exact(tmp_path):
    obo = tmp_path / "exact.obo"
    obo.write_text(EXACT_OBO)
    chroma_path = str(tmp_path / "chroma")
    seed_chromadb(str(obo), chroma_path, embedding_model="default")
    return str(obo), chroma_path


def test_an_exact_synonym_comes_first(tmp_path):
    obo, chroma = _seed_exact(tmp_path)
    hits = search_hpo("Shortness  of-breath", k=3, chroma_path=chroma, obo_path=obo)
    assert hits[0]["hpo_id"] == "HP:0002094" and hits[0]["exact"] is True
    assert hits[0]["ambiguous"] is False
    assert len({h["hpo_id"] for h in hits}) == len(hits) == 3  # no repeat


def test_a_singular_finds_the_plural_name(tmp_path):
    obo, chroma = _seed_exact(tmp_path)
    assert search_hpo("fall", k=2, chroma_path=chroma, obo_path=obo)[0]["hpo_id"] == "HP:0002527"


def test_a_name_of_two_terms_gives_both_marked_ambiguous(tmp_path):
    obo, chroma = _seed_exact(tmp_path)
    hits = search_hpo("same name", k=2, chroma_path=chroma, obo_path=obo)
    assert {h["hpo_id"] for h in hits[:2]} == {"HP:9999901", "HP:9999902"}
    assert all(h["ambiguous"] for h in hits[:2])


def test_without_obo_path_the_search_is_vector_only(tmp_path):
    _, chroma = _seed_exact(tmp_path)
    hits = search_hpo("shortness of breath", k=2, chroma_path=chroma)
    assert not any(h.get("exact") for h in hits)


def test_k_unique_terms_although_each_term_has_several_documents(tmp_path):
    _, chroma = _seed_exact(tmp_path)
    hits = search_hpo("breath", k=6, chroma_path=chroma)
    ids = [h["hpo_id"] for h in hits]
    assert len(ids) == len(set(ids)) == 6  # all 6 terms of the mini ontology
    assert all("#" not in i for i in ids)


def test_an_index_of_the_old_layout_is_not_ready(tmp_path):
    import chromadb

    from phenoscribe.hpo_index import INDEX_SCHEMA, index_ready

    _, chroma = _seed_exact(tmp_path)
    assert index_ready(chroma, "hp/releases/2026-02-16")
    collection = chromadb.PersistentClient(path=chroma).get_collection("hpo_terms")
    meta = dict(collection.metadata)
    meta["index_schema"] = INDEX_SCHEMA - 1
    meta.pop("hnsw:space", None)  # chromadb does not allow a change of the distance
    collection.modify(metadata=meta)
    assert not index_ready(chroma, "hp/releases/2026-02-16")
