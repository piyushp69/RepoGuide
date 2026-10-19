"""ChromaDB (local persistent mode) holding one collection of chunk vectors per project."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from .. import config  # noqa: F401  (sets ANONYMIZED_TELEMETRY before chromadb is imported)
import chromadb
from chromadb.config import Settings as ChromaSettings


def collection_name(project_id: int) -> str:
    return f"repoguide_p{project_id}"


class VectorStore:
    def __init__(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(
            path=str(path), settings=ChromaSettings(anonymized_telemetry=False, allow_reset=False)
        )
        self._lock = threading.Lock()

    def _collection(self, project_id: int):
        with self._lock:
            return self._client.get_or_create_collection(
                name=collection_name(project_id), embedding_function=None, metadata={"hnsw:space": "cosine"}
            )

    def upsert(self, project_id: int, ids: list[str], embeddings: list[list[float]],
               documents: list[str], metadatas: list[dict[str, Any]]) -> None:
        if not ids:
            return
        clean = [{k: ("" if v is None else v) for k, v in m.items()} for m in metadatas]
        self._collection(project_id).upsert(ids=ids, embeddings=embeddings, documents=documents, metadatas=clean)

    def delete(self, project_id: int, ids: list[str]) -> None:
        if not ids:
            return
        collection = self._collection(project_id)
        for start in range(0, len(ids), 500):
            collection.delete(ids=ids[start:start + 500])

    def query(self, project_id: int, embedding: list[float], n: int) -> list[tuple[str, float]]:
        """Nearest chunks as (chunk_id, cosine similarity), best first."""
        collection = self._collection(project_id)
        count = collection.count()
        if count == 0:
            return []
        result = collection.query(query_embeddings=[embedding], n_results=min(n, count), include=["distances"])
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        return [(cid, 1.0 - float(dist)) for cid, dist in zip(ids, distances, strict=True)]

    def count(self, project_id: int) -> int:
        return self._collection(project_id).count()

    def drop(self, project_id: int) -> None:
        with self._lock:
            try:
                self._client.delete_collection(collection_name(project_id))
            except Exception:  # collection may not exist yet
                pass
