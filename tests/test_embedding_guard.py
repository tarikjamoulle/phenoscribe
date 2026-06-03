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
