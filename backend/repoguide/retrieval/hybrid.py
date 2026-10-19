"""Hybrid retrieval.

1. Vector search (Ollama embeddings in ChromaDB) and BM25 keyword search each return
   a ranked candidate list; identifiers or file names mentioned in the question add a
   third "symbol" list.
2. The lists are merged with reciprocal-rank fusion and deduplicated.
3. The top results are expanded with code-graph neighbours (callees, callers and
   imported symbols) so the model sees related code.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from ..config import Settings
from ..db import Database
from ..index.bm25 import STOPWORDS, BM25Cache
from ..index.vectorstore import VectorStore
from ..llm.embedder import Embedder

_SELF_REFERENCE = re.compile(
    r"\b(?:this|our)\s+(?:project|codebase|code\s*base|repo(?:sitory)?|app(?:lication)?|tool|system|software|program)\b"
    r"|\b(?:the|this|our)\s+(?:codebase|code\s*base|repo(?:sitory)?)\b",
    re.IGNORECASE,
)
RRF_K = 60
# BM25 is down-weighted: on the evaluation set dense retrieval ranks better for natural-language
# questions, while BM25 still lifts exact identifiers, error strings and config keys (see DECISIONS.md).
WEIGHTS = {"vector": 1.0, "bm25": 0.5, "symbol": 1.0}
_IDENT = re.compile(r"`([^`]+)`|([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)*)")
_FILE_LIKE = re.compile(r"[\w./-]+\.[A-Za-z]{1,5}\b")


@dataclass
class RetrievedChunk:
    id: str
    file_path: str
    start_line: int
    end_line: int
    symbol: str | None
    symbol_type: str
    language: str
    content: str
    score: float = 0.0
    ranks: dict[str, int] = field(default_factory=dict)
    via: str | None = None  # why a graph neighbour was added, e.g. "called by create_order"

    def summary(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("content")
        return data


@dataclass
class RetrievalResult:
    query: str
    chunks: list[RetrievedChunk]
    seed_count: int
    timings_ms: dict[str, float]
    search_query: str = ""  # the query actually sent to vector / keyword search

    @property
    def seeds(self) -> list[RetrievedChunk]:
        return self.chunks[: self.seed_count]


def reciprocal_rank_fusion(lists: dict[str, list[str]], weights: dict[str, float] | None = None,
                           k: int = RRF_K) -> list[tuple[str, float, dict[str, int]]]:
    """Merge ranked id lists; returns (id, fused score, {list name: rank}) best first."""
    weights = weights or WEIGHTS
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for name, ids in lists.items():
        seen: set[str] = set()
        for rank, item in enumerate(ids, start=1):
            if item in seen:
                continue
            seen.add(item)
            scores[item] = scores.get(item, 0.0) + weights.get(name, 1.0) / (k + rank)
            ranks.setdefault(item, {})[name] = rank
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return [(item, score, ranks[item]) for item, score in ordered]


def _looks_like_identifier(token: str) -> bool:
    if "_" in token or "." in token:
        return True
    if re.search(r"[a-z][A-Z]", token):  # camelCase
        return True
    return token[:1].isupper() and len(token) > 2  # PascalCase class/component names


class HybridRetriever:
    def __init__(self, settings: Settings, db: Database, store: VectorStore, embedder: Embedder, bm25: BM25Cache):
        self.settings = settings
        self.db = db
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25

    def focus_query(self, project_id: int, query: str) -> str:
        """Drop words that refer to the project as a whole (its own name, "this project", "the codebase").

        Every file belongs to the project, so its name carries no signal for finding code inside it, yet
        it pulls BM25 towards docs that mention the name and vector search towards generic chunks.
        """
        project = self.db.query_one("SELECT name, root_path FROM projects WHERE id = ?", (project_id,))
        names: set[str] = set()
        if project:
            base_name = re.sub(r"\(.*?\)", "", project["name"] or "").strip()
            names |= {base_name, base_name.split("/")[-1], PurePosixPath(project["root_path"].replace("\\", "/")).name}
        focused = _SELF_REFERENCE.sub(" ", query)
        for name in sorted((n for n in names if len(n) >= 3), key=len, reverse=True):
            focused = re.sub(rf"(?<![\w-]){re.escape(name)}(?:'s)?(?![\w-])", " ", focused, flags=re.IGNORECASE)
        focused = " ".join(focused.split())
        words = re.findall(r"\w+", focused)
        return focused if len(words) >= 3 else query

    # ----- individual retrievers ------------------------------------------------------------
    def vector_search(self, project_id: int, query: str, n: int) -> list[str]:
        return [cid for cid, _ in self.store.query(project_id, self.embedder.embed_query(query), n)]

    def keyword_search(self, project_id: int, query: str, n: int) -> list[str]:
        return [cid for cid, _ in self.bm25.get(project_id).search(query, n)]

    def symbol_search(self, project_id: int, query: str, n: int) -> list[str]:
        """Chunks defining identifiers (or files) that the question names explicitly."""
        names: list[str] = []
        for quoted, bare in _IDENT.findall(query):
            token = (quoted or bare).strip().rstrip("()")
            if not token or token.lower() in STOPWORDS:
                continue
            if quoted or _looks_like_identifier(token):
                names.append(token)
        hits: list[str] = []
        for name in names:
            short = name.split(".")[-1]
            rows = self.db.query(
                "SELECT chunk_id FROM symbols WHERE project_id = ? AND chunk_id IS NOT NULL AND "
                "(qualified_name = ? OR name = ?) ORDER BY (qualified_name = ?) DESC, start_line",
                (project_id, name, short, name),
            )
            hits += [r["chunk_id"] for r in rows]
        for match in _FILE_LIKE.findall(query):
            base = PurePosixPath(match).name
            rows = self.db.query(
                "SELECT id FROM chunks WHERE project_id = ? AND (file_path = ? OR file_path LIKE ?) "
                "ORDER BY ordinal LIMIT 3",
                (project_id, match, f"%/{base}"),
            )
            hits += [r["id"] for r in rows]
        ordered: list[str] = []
        for cid in hits:
            if cid not in ordered:
                ordered.append(cid)
        return ordered[:n]

    # ----- helpers --------------------------------------------------------------------------------
    def load_chunks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}
        rows: list[dict[str, Any]] = []
        for start in range(0, len(ids), 500):
            batch = ids[start:start + 500]
            rows += self.db.query(
                f"SELECT * FROM chunks WHERE id IN ({','.join('?' * len(batch))})", batch)
        return {r["id"]: r for r in rows}

    @staticmethod
    def _to_retrieved(row: dict[str, Any], score: float, ranks: dict[str, int], via: str | None = None):
        return RetrievedChunk(
            id=row["id"], file_path=row["file_path"], start_line=row["start_line"], end_line=row["end_line"],
            symbol=row["symbol"], symbol_type=row["symbol_type"], language=row["language"],
            content=row["content"], score=score, ranks=ranks, via=via,
        )

    def neighbours(self, project_id: int, seeds: list[RetrievedChunk], exclude: set[str]) -> list[RetrievedChunk]:
        """Callees, callers and imported symbols of the given seeds (at most two per seed)."""
        picked: list[tuple[str, str, float]] = []  # (chunk id, reason, score)
        taken = set(exclude)
        for seed in seeds:
            label = seed.symbol or PurePosixPath(seed.file_path).name
            rows = self.db.query(
                "SELECT dst_chunk_id AS cid, 'callee' AS rel, confidence FROM edges WHERE project_id = ? "
                "AND kind = 'call' AND src_chunk_id = ? AND dst_chunk_id IS NOT NULL "
                "UNION ALL SELECT src_chunk_id AS cid, 'caller' AS rel, confidence FROM edges WHERE project_id = ? "
                "AND kind = 'call' AND dst_chunk_id = ? AND src_chunk_id IS NOT NULL "
                "ORDER BY confidence DESC",
                (project_id, seed.id, project_id, seed.id),
            )
            if not rows and seed.symbol_type in ("module", "class"):
                rows = self.db.query(
                    "SELECT dst_chunk_id AS cid, 'import' AS rel, confidence FROM edges WHERE project_id = ? "
                    "AND kind = 'import' AND src_file = ? AND dst_chunk_id IS NOT NULL LIMIT 4",
                    (project_id, seed.file_path),
                )
            count = 0
            for row in rows:
                if row["cid"] in taken:
                    continue
                reason = {"callee": f"called by {label}", "caller": f"calls {label}",
                          "import": f"imported by {PurePosixPath(seed.file_path).name}"}[row["rel"]]
                picked.append((row["cid"], reason, seed.score * 0.5 * float(row["confidence"])))
                taken.add(row["cid"])
                count += 1
                if count >= 2:
                    break
            if len(picked) >= self.settings.graph_max_neighbors:
                break
        picked = picked[: self.settings.graph_max_neighbors]
        rows_by_id = self.load_chunks([cid for cid, _, _ in picked])
        return [self._to_retrieved(rows_by_id[cid], score, {}, via=reason)
                for cid, reason, score in picked if cid in rows_by_id]

    # ----- main entry point ---------------------------------------------------------------------
    def retrieve(self, project_id: int, query: str, top_k: int | None = None, mode: str = "hybrid",
                 expand: bool = True) -> RetrievalResult:
        top_k = top_k or self.settings.retrieval_top_k
        n = max(self.settings.retrieval_candidates, top_k)
        timings: dict[str, float] = {}
        lists: dict[str, list[str]] = {}
        search_query = self.focus_query(project_id, query)
        if mode in ("hybrid", "vector"):
            t0 = time.perf_counter()
            lists["vector"] = self.vector_search(project_id, search_query, n)
            timings["vector"] = (time.perf_counter() - t0) * 1000
        if mode in ("hybrid", "bm25"):
            t0 = time.perf_counter()
            lists["bm25"] = self.keyword_search(project_id, search_query, n)
            timings["bm25"] = (time.perf_counter() - t0) * 1000
        if mode == "hybrid":
            lists["symbol"] = self.symbol_search(project_id, query, n)  # explicit identifiers: original wording

        fused = reciprocal_rank_fusion(lists)
        # Fusion rewards agreement, so a chunk that one retriever ranks first but the other
        # misses can sink below mediocre "both lists" hits. Each retriever's best hit is
        # therefore guaranteed a place among the seeds.
        chosen = [item for item in fused[:top_k]]
        chosen_ids = {item[0] for item in chosen}
        by_id = {item[0]: item for item in fused}
        for name in ("symbol", "bm25", "vector"):
            best = lists.get(name, [])[:1]
            if best and best[0] not in chosen_ids and best[0] in by_id:
                for index in range(len(chosen) - 1, -1, -1):
                    if not any(chosen[index][0] == (lists.get(n, [])[:1] or [None])[0] for n in lists):
                        chosen_ids.discard(chosen[index][0])
                        chosen[index] = by_id[best[0]]
                        chosen_ids.add(best[0])
                        break
        chosen.sort(key=lambda item: -item[1])
        chosen += [item for item in fused[top_k:] if item[0] not in chosen_ids]  # fallbacks if rows are missing
        rows_by_id = self.load_chunks([cid for cid, _, _ in chosen[: top_k * 2]])
        seeds: list[RetrievedChunk] = []
        for cid, score, ranks in chosen:
            row = rows_by_id.get(cid)
            if row is None:  # vector store may briefly be ahead of SQLite during re-indexing
                continue
            seeds.append(self._to_retrieved(row, score, ranks))
            if len(seeds) >= top_k:
                break
        chunks = list(seeds)
        if expand and seeds:
            t0 = time.perf_counter()
            # Expand each retriever's best hit first, then the other top fused seeds.
            anchors = {ids[0] for ids in lists.values() if ids}
            expansion = [s for s in seeds if s.id in anchors]
            expansion += [s for s in seeds[: self.settings.graph_expand_seeds] if s.id not in anchors]
            chunks += self.neighbours(project_id, expansion, {c.id for c in seeds})
            timings["graph"] = (time.perf_counter() - t0) * 1000
        return RetrievalResult(query=query, chunks=chunks, seed_count=len(seeds), timings_ms=timings,
                               search_query=search_query)
