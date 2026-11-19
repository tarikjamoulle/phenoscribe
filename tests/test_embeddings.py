"""Tests for the embedding-model selector.

The shared index uses ChromaDB's general-domain default (all-MiniLM-L6-v2).
SapBERT is a biomedical option. These tests check that the selector picks the
right function without downloading any model.
"""

import pytest

from phenoscribe.embeddings import SapBERTEmbeddingFunction, get_embedding_function


def test_default_returns_none_so_chromadb_uses_its_builtin():
    # None tells ChromaDB to keep its default ONNX function, the same one the
    # shared index was seeded with.
    assert get_embedding_function("default") is None


def test_sapbert_returns_sapbert_function_without_loading_model():
    fn = get_embedding_function("sapbert")

    assert isinstance(fn, SapBERTEmbeddingFunction)
    # Lazy load: constructing the selector must not pull weights.
    assert fn._model is None
    assert fn._tokenizer is None


def test_unknown_model_raises():
    with pytest.raises(ValueError, match="Unknown embedding_model"):
        get_embedding_function("medcpt-not-wired")


def test_sapbert_config_roundtrip():
    fn = get_embedding_function("sapbert")
    rebuilt = SapBERTEmbeddingFunction.build_from_config(fn.get_config())

    assert rebuilt._model_name == fn._model_name
    assert rebuilt._max_length == fn._max_length
