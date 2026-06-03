"""Embedding functions for the HPO index.

The shared index uses ChromaDB's default ONNX function (all-MiniLM-L6-v2),
a general-domain 384-dim model. SapBERT (Liu et al. 2021, NAACL) is trained
by self-alignment on UMLS synonym pairs, so biomedical entities that share a
concept land close together. That is what we want for rare phenotypes where
the surface forms barely differ (episodic vs spinocerebellar vs cerebellar
ataxia).

Reference SapBERT usage (HF model card): take the [CLS] token of the last
layer as the embedding. Short entity names use max_length=25; our index text
also carries synonyms and a definition, so we allow a longer window.
"""

from __future__ import annotations

from typing import Any

SAPBERT_MODEL = "cambridgeltl/SapBERT-from-PubMedBERT-fulltext"


class SapBERTEmbeddingFunction:
    """ChromaDB embedding function backed by SapBERT (CLS pooling).

    Lazy-loads transformers + torch on first call so importing this module
    stays cheap and the default path never pays for the biomedical model.
    """

    def __init__(
        self,
        model_name: str = SAPBERT_MODEL,
        max_length: int = 64,
        batch_size: int = 64,
        device: str | None = None,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._batch_size = batch_size
        self._device = device
        self._tokenizer = None
        self._model = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import AutoModel, AutoTokenizer

        if self._device is None:
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._tokenizer = AutoTokenizer.from_pretrained(self._model_name)
        self._model = AutoModel.from_pretrained(self._model_name).to(self._device)
        self._model.eval()

    def __call__(self, input: list[str]) -> list[list[float]]:
        self._ensure_loaded()
        import torch

        embeddings: list[list[float]] = []
        for start in range(0, len(input), self._batch_size):
            batch = input[start : start + self._batch_size]
            toks = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self._max_length,
                return_tensors="pt",
            ).to(self._device)
            with torch.no_grad():
                out = self._model(**toks)
            # CLS representation = last_hidden_state[:, 0, :]
            cls = out[0][:, 0, :]
            embeddings.extend(cls.cpu().float().numpy().tolist())
        return embeddings

    def embed_query(self, input: list[str]) -> list[list[float]]:
        # SapBERT uses one encoder for entities and queries, so query and
        # document embeddings come from the same path.
        return self.__call__(input)

    @staticmethod
    def name() -> str:
        return "sapbert"

    def get_config(self) -> dict[str, Any]:
        return {
            "model_name": self._model_name,
            "max_length": self._max_length,
            "batch_size": self._batch_size,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "SapBERTEmbeddingFunction":
        return SapBERTEmbeddingFunction(
            model_name=config.get("model_name", SAPBERT_MODEL),
            max_length=config.get("max_length", 64),
            batch_size=config.get("batch_size", 64),
        )

    def default_space(self) -> str:
        return "cosine"

    def supported_spaces(self) -> list[str]:
        return ["cosine", "l2", "ip"]


# Registered model keys for the hpo_index.embedding_model config option.
EMBEDDING_MODELS = ("default", "sapbert")


def get_embedding_function(model: str = "default", **kwargs: Any):
    """Return a ChromaDB embedding function for the given model key.

    "default" returns None, which tells ChromaDB to use its built-in ONNX
    function (all-MiniLM-L6-v2) — the same behaviour the shared index was
    seeded with. "sapbert" returns the biomedical function.
    """
    if model == "default":
        return None
    if model == "sapbert":
        return SapBERTEmbeddingFunction(**kwargs)
    raise ValueError(
        f"Unknown embedding_model {model!r}. Expected one of {EMBEDDING_MODELS}."
    )
