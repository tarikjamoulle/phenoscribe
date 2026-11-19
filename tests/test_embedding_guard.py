"""Guard against querying a seeded ChromaDB index with the wrong encoder.

The shipped collection is all-MiniLM (embedding_model='default'), but config
allows hpo_index.embedding_model: sapbert. Without a rebuild of the index, that
path queries a MiniLM index with SapBERT vectors, which gives wrong results.
The guard in search_hpo stops with an error that names the rebuild step.
"""

import pytest

from phenoscribe.hpo_index import EmbeddingModelMismatch, search_hpo


def test_configured_model_mismatch_raises_with_reseed_message(mini_index):
    chroma_path = mini_index

    with pytest.raises(EmbeddingModelMismatch) as exc:
        search_hpo("fievre", k=2, chroma_path=chroma_path, embedding_model="sapbert")

    msg = str(exc.value)
    # Names both models so the user can see what they asked for vs what is on disk.
    assert "sapbert" in msg
    assert "default" in msg
    # Names the command that rebuilds the index for the configured encoder.
    assert "phenoscribe setup" in msg


def test_matching_model_does_not_raise(mini_index):
    chroma_path = mini_index

    # Configured model equals the seeded model: no guard, real retrieval runs.
    results = search_hpo(
        "fever", k=2, chroma_path=chroma_path, embedding_model="default"
    )
    assert results[0]["hpo_id"] == "HP:0001945"


def test_none_reads_seeded_model_and_does_not_raise(mini_index):
    chroma_path = mini_index

    # When the caller passes None, the seeded model is read back: never a mismatch.
    results = search_hpo("headache", k=2, chroma_path=chroma_path, embedding_model=None)
    assert results[0]["hpo_id"] == "HP:0002315"
