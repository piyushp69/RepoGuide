"""BM25 keyword search over chunks, with code-aware tokenisation.

Identifiers are split on snake_case and camelCase boundaries (keeping the whole
identifier too) and English words are stemmed, so "discount codes applied" matches
``apply_discount`` and ``DISCOUNT_CODES``.
"""

from __future__ import annotations

import re
import threading
from functools import lru_cache

import numpy as np
import snowballstemmer
from rank_bm25 import BM25Okapi

from ..db import Database

STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "else", "of", "to", "in", "on", "for", "with", "by",
    "at", "from", "as", "is", "are", "was", "were", "be", "been", "being", "it", "its", "this", "that",
    "these", "those", "there", "here", "what", "which", "who", "whom", "how", "why", "when", "where", "does",
    "do", "did", "done", "doing", "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "i", "you", "he", "she", "we", "they", "me", "my", "your", "our", "their", "them", "us", "about", "into",
    "over", "under", "than", "so", "not", "no", "yes", "all", "any", "some", "each", "every", "more", "most",
    "other", "such", "only", "own", "same", "too", "very", "just", "also", "between", "after", "before",
    "during", "while", "up", "down", "out", "off", "again", "further", "once", "have", "has", "had", "having",
    "get", "gets", "got", "use", "used", "using", "work", "works", "happen", "happens", "code", "codebase",
    "project", "file", "files", "function", "functions", "method", "methods", "class", "classes", "explain",
    "show", "tell", "self", "def", "return", "import", "none", "true", "false", "null", "undefined", "const",
    "let", "var", "new", "async", "await", "export", "default", "lambda", "pass", "elif", "try", "except",
    "raise", "throw", "catch", "yield", "void", "public", "private", "protected", "static", "str", "int",
    "float", "bool", "string", "number",
}

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*|\d+")
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
_local = threading.local()


@lru_cache(maxsize=200_000)
def _stem(word: str) -> str:
    stemmer = getattr(_local, "stemmer", None)
    if stemmer is None:
        stemmer = _local.stemmer = snowballstemmer.stemmer("english")
    return stemmer.stemWord(word)


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for word in _WORD.findall(text):
        parts: list[str] = []
        for piece in word.split("_"):
            if piece:
                parts.extend(_CAMEL.findall(piece))
        lowered = [p.lower() for p in parts]
        whole = word.strip("_").lower()
        if len(lowered) > 1 and whole and whole not in STOPWORDS:
            tokens.append(whole)
        for part in lowered:
            if len(part) < 2 or part in STOPWORDS:
                continue
            tokens.append(_stem(part))
    return tokens


class BM25Index:
    def __init__(self, ids: list[str], texts: list[str]):
        self.ids = ids
        corpus = [tokenize(t) for t in texts]
        self._bm25 = BM25Okapi(corpus) if any(corpus) else None

    def search(self, query: str, n: int) -> list[tuple[str, float]]:
        if self._bm25 is None:
            return []
        terms = tokenize(query)
        if not terms:
            return []
        scores = self._bm25.get_scores(terms)
        order = np.argsort(-scores)[:n]
        return [(self.ids[i], float(scores[i])) for i in order if scores[i] > 0]


class BM25Cache:
    """Builds a project's BM25 index lazily and rebuilds it when the index version changes."""

    def __init__(self, db: Database):
        self.db = db
        self._entries: dict[int, tuple[int, BM25Index]] = {}
        self._lock = threading.Lock()

    def get(self, project_id: int) -> BM25Index:
        version = self.db.scalar("SELECT index_version FROM projects WHERE id = ?", (project_id,)) or 0
        with self._lock:
            entry = self._entries.get(project_id)
            if entry and entry[0] == version:
                return entry[1]
        rows = self.db.query(
            "SELECT id, file_path, symbol, content FROM chunks WHERE project_id = ? ORDER BY file_path, start_line",
            (project_id,),
        )
        index = BM25Index(
            [r["id"] for r in rows],
            [f"{r['file_path']} {r['symbol'] or ''}\n{r['content']}" for r in rows],
        )
        with self._lock:
            self._entries[project_id] = (version, index)
        return index

    def invalidate(self, project_id: int) -> None:
        with self._lock:
            self._entries.pop(project_id, None)
