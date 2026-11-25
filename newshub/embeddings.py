"""Turn headlines into embeddings: lists of 384 numbers that capture meaning.

Two headlines about the same event ("Nvidia beats estimates" and "Nvidia tops
Wall Street forecasts") end up close together even though they share few words.
That closeness is what the duplicate grouping in cluster.py measures.

Model: sentence-transformers/all-MiniLM-L6-v2, run locally through fastembed
(ONNX runtime, no GPU, no API key, no cost). The first run downloads the model
(about 90 MB) into MODEL_CACHE; later runs reuse it.
"""

from __future__ import annotations

import math
import os
from typing import Protocol

from .config import ROOT

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
DIMENSIONS = 384
MODEL_CACHE = os.environ.get("NEWSHUB_MODEL_CACHE", str(ROOT / ".cache" / "fastembed"))


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class FastEmbedder:
    """Wraps fastembed. The model loads on first use, so runs with nothing new to embed stay fast."""

    def __init__(self, model_name: str = MODEL_NAME, cache_dir: str = MODEL_CACHE):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None

    def _load(self):
        if self._model is None:
            from fastembed import TextEmbedding  # imported here: it is slow to import

            self._model = TextEmbedding(model_name=self.model_name, cache_dir=self.cache_dir)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().embed(texts, batch_size=64)
        return [normalize([float(x) for x in v]) for v in vectors]


def normalize(vector: list[float]) -> list[float]:
    """Scale a vector to length 1, so cosine similarity is a plain dot product."""
    length = math.sqrt(sum(x * x for x in vector))
    return [x / length for x in vector] if length else vector


def to_pgvector(vector: list[float]) -> str:
    """pgvector's text format: '[0.012345,-0.067890,...]'."""
    return "[" + ",".join(f"{x:.6f}" for x in vector) + "]"
