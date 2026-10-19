"""Incremental indexing pipeline: scan -> parse/chunk -> embed -> code graph.

Each file is hashed; only new or changed files are re-parsed, and only chunks whose
content or position changed are re-embedded. Chunks of deleted files are removed.
"""

from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..config import Settings
from ..db import Database
from ..graph.resolver import ChunkLocator, FileInfo, GraphBuilder
from ..ingest.sources import SourceSpec, sync_github
from ..ingest.walker import read_text, walk_project
from ..jobs import JobReporter
from ..llm.embedder import Embedder
from ..parsing.chunker import ChunkConfig, parse_file
from ..parsing.models import Chunk
from ..util import dumps, loads, sha256_bytes, short_hash, utcnow
from .bm25 import BM25Cache
from .vectorstore import VectorStore
from ..errors import NotFoundError

log = logging.getLogger(__name__)


def make_chunk_id(project_id: int, chunk: Chunk) -> str:
    return short_hash(project_id, chunk.file_path, chunk.start_line, chunk.end_line, chunk.symbol or "",
                      short_hash(chunk.content, length=16))


def embedding_text(row: dict[str, Any]) -> str:
    """Text sent to the embedding model: location and symbol first, then the code."""
    label = f"{row['symbol_type']} {row['symbol']}" if row.get("symbol") else row["symbol_type"]
    return f"File: {row['file_path']}\n{label} ({row['language']})\n\n{row['content']}"


class Indexer:
    def __init__(self, settings: Settings, db: Database, store: VectorStore, embedder: Embedder, bm25: BM25Cache):
        self.settings = settings
        self.db = db
        self.store = store
        self.embedder = embedder
        self.bm25 = bm25
        self.chunk_config = ChunkConfig(
            chunk_max_lines=settings.chunk_max_lines,
            class_inline_max_lines=settings.class_inline_max_lines,
            window_lines=settings.window_lines,
            window_overlap=settings.window_overlap,
        )

    # ---------------------------------------------------------------------------------------
    def index_project(self, project_id: int, reporter: JobReporter, full: bool = False) -> dict[str, Any]:
        project = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            raise NotFoundError(f"Project {project_id} does not exist")
        self.db.execute("UPDATE projects SET status = 'indexing', error = NULL, updated_at = ? WHERE id = ?",
                        (utcnow(), project_id))
        try:
            stats = self._index(project, reporter, full)
        except Exception as exc:
            self.db.execute("UPDATE projects SET status = 'error', error = ?, updated_at = ? WHERE id = ?",
                            (str(exc)[:2000], utcnow(), project_id))
            raise
        return stats

    def _index(self, project: dict[str, Any], reporter: JobReporter, full: bool) -> dict[str, Any]:
        project_id = project["id"]
        root = Path(project["root_path"])

        if project["source_type"] == "github":
            reporter.update(phase="cloning", message=f"Fetching {project['source']}")
            spec = SourceSpec(source_type="github", source=project["source"], name=project["name"],
                              root_path=root, clone_url=_clone_url(project["source"]), branch=project.get("branch"))
            sync_github(spec, self.settings.git_timeout_seconds)
        if not root.is_dir():
            raise FileNotFoundError(f"Project folder no longer exists: {root}")

        embed_model_changed = bool(project.get("embed_model")) and project["embed_model"] != self.embedder.model_name
        if full or embed_model_changed:
            reporter.update(phase="resetting", message="Clearing previous index")
            self.store.drop(project_id)
            with self.db.tx() as conn:
                conn.execute("UPDATE chunks SET embedded = 0 WHERE project_id = ?", (project_id,))
                if full:
                    conn.execute("DELETE FROM chunks WHERE project_id = ?", (project_id,))
                    conn.execute("DELETE FROM symbols WHERE project_id = ?", (project_id,))
                    conn.execute("DELETE FROM files WHERE project_id = ?", (project_id,))

        reporter.update(phase="scanning", message=f"Scanning {root}")
        walk = walk_project(root, max_file_bytes=self.settings.max_file_bytes, max_files=self.settings.max_files)
        stats = self._parse_changed(project_id, walk.files, reporter)
        stats["skipped"] = dict(walk.skipped)
        stats["truncated"] = walk.truncated
        stats["embedded"] = self._embed_pending(project_id, reporter)
        stats["edges"] = self._rebuild_graph(project_id, root, reporter)

        changed = bool(stats["added"] or stats["changed"] or stats["removed"] or stats["embedded"] or full)
        counts = self.db.query_one(
            "SELECT (SELECT COUNT(*) FROM files WHERE project_id = ?) AS files, "
            "(SELECT COUNT(*) FROM chunks WHERE project_id = ?) AS chunks",
            (project_id, project_id),
        ) or {"files": 0, "chunks": 0}
        self.db.execute(
            "UPDATE projects SET status = 'ready', error = NULL, embed_model = ?, file_count = ?, chunk_count = ?, "
            "skipped_json = ?, index_version = index_version + ?, last_indexed_at = ?, updated_at = ? WHERE id = ?",
            (self.embedder.model_name, counts["files"], counts["chunks"], dumps(stats["skipped"]),
             1 if changed or not project["index_version"] else 0, utcnow(), utcnow(), project_id),
        )
        self.bm25.invalidate(project_id)
        stats.update({"files": counts["files"], "chunks": counts["chunks"], "changed_any": changed})
        reporter.update(phase="indexed", message=(
            f"{counts['files']} files, {counts['chunks']} chunks ({stats['added']} added, "
            f"{stats['changed']} changed, {stats['removed']} removed, {stats['unchanged']} unchanged)"
        ), force=True)
        return stats

    # ---------------------------------------------------------------------------------------
    def _parse_changed(self, project_id: int, files: list, reporter: JobReporter) -> dict[str, Any]:
        known = {r["path"]: r["hash"] for r in self.db.query(
            "SELECT path, hash FROM files WHERE project_id = ?", (project_id,))}
        seen: set[str] = set()
        stats = {"added": 0, "changed": 0, "unchanged": 0, "removed": 0, "parse_errors": 0}
        stale_vector_ids: list[str] = []
        total = len(files)
        reporter.update(phase="parsing", current=0, total=total, message="Parsing and chunking files")

        for index, discovered in enumerate(files, start=1):
            seen.add(discovered.rel_path)
            reporter.update(current=index, total=total, message=discovered.rel_path)
            try:
                raw, text = read_text(discovered.abs_path)
            except OSError as exc:
                log.warning("Cannot read %s: %s", discovered.abs_path, exc)
                stats["parse_errors"] += 1
                continue
            digest = sha256_bytes(raw)
            previous = known.get(discovered.rel_path)
            if previous == digest:
                stats["unchanged"] += 1
                continue
            parsed = parse_file(discovered.rel_path, text, discovered.language, self.chunk_config)
            stale_vector_ids += self._store_file(project_id, discovered, digest, len(raw), parsed)
            stats["changed" if previous else "added"] += 1

        removed = [path for path in known if path not in seen]
        for path in removed:
            ids = [r["id"] for r in self.db.query(
                "SELECT id FROM chunks WHERE project_id = ? AND file_path = ?", (project_id, path))]
            stale_vector_ids += ids
            with self.db.tx() as conn:
                conn.execute("DELETE FROM chunks WHERE project_id = ? AND file_path = ?", (project_id, path))
                conn.execute("DELETE FROM symbols WHERE project_id = ? AND file_path = ?", (project_id, path))
                conn.execute("DELETE FROM files WHERE project_id = ? AND path = ?", (project_id, path))
        stats["removed"] = len(removed)
        self.store.delete(project_id, stale_vector_ids)
        return stats

    def _store_file(self, project_id: int, discovered, digest: str, size: int, parsed) -> list[str]:
        """Replace a file's chunks and symbols; returns chunk ids whose vectors must be deleted."""
        path = discovered.rel_path
        new_rows: list[tuple] = []
        new_ids: list[str] = []
        for ordinal, chunk in enumerate(parsed.chunks):
            chunk_id = make_chunk_id(project_id, chunk)
            if chunk_id in new_ids:
                continue
            new_ids.append(chunk_id)
            new_rows.append((chunk_id, project_id, path, chunk.start_line, chunk.end_line, chunk.symbol,
                             chunk.symbol_type, chunk.language, chunk.content, ordinal))
        locator = ChunkLocator([{"id": r[0], "file_path": path, "start_line": r[3], "end_line": r[4]}
                                for r in new_rows])
        symbol_rows = [
            (project_id, path, s.name, s.qualified_name, s.kind, s.parent, s.start_line, s.end_line,
             locator.chunk_at(path, s.start_line), s.docstring)
            for s in parsed.symbols
        ]
        with self.db.tx() as conn:
            old_ids = {r[0] for r in conn.execute(
                "SELECT id FROM chunks WHERE project_id = ? AND file_path = ?", (project_id, path))}
            new_id_set = set(new_ids)
            stale = [cid for cid in old_ids if cid not in new_id_set]
            if stale:
                conn.executemany("DELETE FROM chunks WHERE id = ?", [(cid,) for cid in stale])
            # Unchanged chunks (same id) keep their embedding; others are inserted unembedded.
            conn.executemany(
                "INSERT INTO chunks (id, project_id, file_path, start_line, end_line, symbol, symbol_type, language, "
                "content, ordinal, embedded) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT(id) DO UPDATE SET ordinal = excluded.ordinal",
                new_rows,
            )
            conn.execute("DELETE FROM symbols WHERE project_id = ? AND file_path = ?", (project_id, path))
            conn.executemany(
                "INSERT INTO symbols (project_id, file_path, name, qualified_name, kind, parent, start_line, end_line, "
                "chunk_id, docstring) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                symbol_rows,
            )
            conn.execute(
                "INSERT INTO files (project_id, path, hash, language, size, line_count, parse_json, indexed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, path) DO UPDATE SET hash = excluded.hash, "
                "language = excluded.language, size = excluded.size, line_count = excluded.line_count, "
                "parse_json = excluded.parse_json, indexed_at = excluded.indexed_at",
                (project_id, path, digest, discovered.language, size, parsed.line_count,
                 dumps(parsed.graph_payload()), utcnow()),
            )
        return stale

    # ---------------------------------------------------------------------------------------
    def _embed_pending(self, project_id: int, reporter: JobReporter) -> int:
        pending = self.db.query(
            "SELECT id, file_path, start_line, end_line, symbol, symbol_type, language, content FROM chunks "
            "WHERE project_id = ? AND embedded = 0 ORDER BY file_path, start_line",
            (project_id,),
        )
        total = len(pending)
        reporter.update(phase="embedding", current=0, total=total,
                        message=f"Embedding {total} chunks with {self.embedder.model_name}")
        batch_size = max(1, self.settings.embed_batch_size)
        done = 0
        for start in range(0, total, batch_size):
            batch = pending[start:start + batch_size]
            vectors = self.embedder.embed_documents([embedding_text(r) for r in batch])
            self.store.upsert(
                project_id,
                ids=[r["id"] for r in batch],
                embeddings=vectors,
                documents=[r["content"] for r in batch],
                metadatas=[{k: r[k] for k in ("file_path", "start_line", "end_line", "symbol", "symbol_type",
                                               "language")} for r in batch],
            )
            with self.db.tx() as conn:
                conn.executemany("UPDATE chunks SET embedded = 1 WHERE id = ?", [(r["id"],) for r in batch])
            done += len(batch)
            reporter.update(current=done, total=total)
        return done

    def _rebuild_graph(self, project_id: int, root: Path, reporter: JobReporter) -> int:
        reporter.update(phase="graph", message="Building import and call graph")
        symbols_by_file: dict[str, list[dict[str, Any]]] = {}
        for row in self.db.query(
            "SELECT file_path, name, qualified_name, kind, parent, start_line, end_line, chunk_id FROM symbols "
            "WHERE project_id = ?", (project_id,),
        ):
            symbols_by_file.setdefault(row["file_path"], []).append(row)
        files: dict[str, FileInfo] = {}
        for row in self.db.query("SELECT path, language, parse_json FROM files WHERE project_id = ?", (project_id,)):
            payload = loads(row["parse_json"], {}) or {}
            files[row["path"]] = FileInfo(
                path=row["path"], language=row["language"], grammar=payload.get("grammar"),
                symbols=symbols_by_file.get(row["path"], []), imports=payload.get("imports", []),
                calls=payload.get("calls", []), default_export=payload.get("default_export"),
                has_main_guard=bool(payload.get("has_main_guard")),
            )
        chunks = self.db.query("SELECT id, file_path, start_line, end_line FROM chunks WHERE project_id = ?",
                               (project_id,))
        edges = GraphBuilder(files, chunks, root).build()
        with self.db.tx() as conn:
            conn.execute("DELETE FROM edges WHERE project_id = ?", (project_id,))
            conn.executemany(
                "INSERT INTO edges (project_id, kind, src_file, src_symbol, src_chunk_id, dst_file, dst_symbol, "
                "dst_chunk_id, external, line, confidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(project_id, *(asdict(e)[k] for k in ("kind", "src_file", "src_symbol", "src_chunk_id", "dst_file",
                                                       "dst_symbol", "dst_chunk_id", "external", "line",
                                                       "confidence"))) for e in edges],
            )
        reporter.update(message=f"{len(edges)} graph edges")
        return len(edges)


def _clone_url(source: str) -> str:
    base = source.split("/tree/")[0].rstrip("/")
    return base + ".git"
