"""Embedding models."""

from __future__ import annotations

from typing import Protocol

from .ollama import OllamaClient


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class OllamaEmbedder:
    """Embeds with a local Ollama model, adding the task prefixes the model was trained with."""

    def __init__(self, client: OllamaClient, model: str, doc_prefix: str = "", query_prefix: str = "",
                 batch_size: int = 16, max_chars: int = 6000):
        self.client = client
        self.model_name = model
        self.doc_prefix = doc_prefix
        self.query_prefix = query_prefix
        self.batch_size = max(1, batch_size)
        self.max_chars = max_chars
        self._query_cache: dict[str, list[float]] = {}  # repeated questions (chat retries, evaluation)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = [self.doc_prefix + t[: self.max_chars] for t in texts[start:start + self.batch_size]]
            vectors.extend(self.client.embed(self.model_name, batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        key = text[: self.max_chars]
        cached = self._query_cache.get(key)
        if cached is not None:
            return cached
        vector = self.client.embed(self.model_name, [self.query_prefix + key])[0]
        if len(self._query_cache) >= 512:
            self._query_cache.pop(next(iter(self._query_cache)))
        self._query_cache[key] = vector
        return vector
