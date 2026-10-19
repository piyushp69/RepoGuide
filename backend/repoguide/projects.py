"""Project registry: add, list, index, delete, and read indexed files safely."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ingest.sources import parse_source
from .ingest.walker import read_text
from .llm.ollama import OllamaError
from .parsing.chunker import split_lines
from .util import loads, utcnow
from .errors import ConflictError, InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from .jobs import JobReporter
    from .services import Services

log = logging.getLogger(__name__)


class ProjectService:
    def __init__(self, services: "Services"):
        self.s = services

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        row = dict(row)
        row["skipped"] = loads(row.pop("skipped_json", None), {}) or {}
        row["job"] = self.s.jobs.latest(row["id"], "index")
        row["has_overview"] = bool(self.s.db.scalar("SELECT 1 FROM overviews WHERE project_id = ?", (row["id"],)))
        return row

    def list(self) -> list[dict[str, Any]]:
        return [self._view(r) for r in self.s.db.query("SELECT * FROM projects ORDER BY updated_at DESC, id DESC")]

    def get(self, project_id: int) -> dict[str, Any] | None:
        row = self.s.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._view(row) if row else None

    def create(self, source: str, name: str | None = None) -> dict[str, Any]:
        spec = parse_source(source, self.s.settings.workspace_dir)
        existing = self.s.db.query_one("SELECT * FROM projects WHERE root_path = ?", (str(spec.root_path),))
        if existing:
            return self._view(existing)
        now = utcnow()
        project_id = self.s.db.execute(
            "INSERT INTO projects (name, source_type, source, root_path, branch, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'new', ?, ?)",
            ((name or "").strip() or spec.name, spec.source_type, spec.source, str(spec.root_path), spec.branch,
             now, now),
        )
        return self.get(project_id)  # type: ignore[return-value]

    def rename(self, project_id: int, name: str) -> dict[str, Any] | None:
        name = name.strip()
        if not name:
            raise InvalidInputError("Name cannot be empty")
        self.s.db.execute("UPDATE projects SET name = ?, updated_at = ? WHERE id = ?", (name, utcnow(), project_id))
        return self.get(project_id)

    def start_index(self, project_id: int, full: bool = False) -> dict[str, Any]:
        return self.s.jobs.submit("index", lambda reporter: self.index_job(project_id, reporter, full),
                                  project_id=project_id)

    def index_job(self, project_id: int, reporter: "JobReporter", full: bool = False) -> dict[str, Any]:
        """Index, then (re)generate the cached overview when the code changed or none exists."""
        stats = self.s.indexer.index_project(project_id, reporter, full)
        if stats.get("changed_any") or not self.s.overview.is_current(project_id):
            # The index is usable even if the overview fails; report the failure instead of hiding the index.
            try:
                result = self.s.overview.generate(project_id, reporter)
                stats["overview_warnings"] = result["warnings"]
            except OllamaError as exc:
                log.warning("Overview generation failed for project %s: %s", project_id, exc)
                stats["overview_warnings"] = [f"Overview generation failed: {exc}"]
            except Exception as exc:
                log.exception("Overview generation crashed for project %s", project_id)
                stats["overview_warnings"] = [f"Overview generation failed: {exc}"]
        return stats

    def delete(self, project_id: int) -> bool:
        project = self.s.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            return False
        if self.s.jobs.active(project_id):
            raise ConflictError("Wait for the running job to finish before deleting this project.")
        self.s.store.drop(project_id)
        self.s.bm25.invalidate(project_id)
        with self.s.db.tx() as conn:
            conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        root = Path(project["root_path"]).resolve()
        workspace = self.s.settings.workspace_dir.resolve()
        # Only folders RepoGuide cloned itself are removed; local folders are never touched.
        if project["source_type"] == "github" and workspace in root.parents:
            shutil.rmtree(root, ignore_errors=True)
        return True

    # ----- files ---------------------------------------------------------------------------------
    def files(self, project_id: int) -> list[dict[str, Any]]:
        return self.s.db.query(
            "SELECT f.path, f.language, f.line_count, f.size, (SELECT COUNT(*) FROM chunks c WHERE c.project_id = "
            "f.project_id AND c.file_path = f.path) AS chunks FROM files f WHERE f.project_id = ? ORDER BY f.path",
            (project_id,),
        )

    def symbols(self, project_id: int, path: str | None = None) -> list[dict[str, Any]]:
        sql = ("SELECT file_path, name, qualified_name, kind, parent, start_line, end_line, chunk_id, docstring "
               "FROM symbols WHERE project_id = ?")
        params: list[Any] = [project_id]
        if path:
            sql += " AND file_path = ?"
            params.append(path)
        return self.s.db.query(sql + " ORDER BY file_path, start_line", params)

    def read_file(self, project_id: int, path: str, start: int | None = None, end: int | None = None,
                  context: int = 0) -> dict[str, Any]:
        """Lines of an indexed file. Only paths in the index can be read (no traversal)."""
        project = self.s.db.query_one("SELECT root_path FROM projects WHERE id = ?", (project_id,))
        row = self.s.db.query_one("SELECT path, language FROM files WHERE project_id = ? AND path = ?",
                                  (project_id, path))
        if project is None or row is None:
            raise NotFoundError("File is not part of this project's index")
        root = Path(project["root_path"]).resolve()
        full = (root / row["path"]).resolve()
        if root not in full.parents:
            raise NotFoundError("File is outside the project")
        if not full.is_file():
            raise NotFoundError("File no longer exists on disk; re-index the project")
        lines = split_lines(read_text(full)[1])
        total = len(lines)
        start = max(1, start or 1)
        end = min(total, end or total)
        view_start = max(1, start - context)
        view_end = min(total, end + context)
        return {"path": row["path"], "language": row["language"], "total_lines": total,
                "start_line": start, "end_line": end, "view_start": view_start, "view_end": view_end,
                "lines": lines[view_start - 1:view_end]}

    def related(self, project_id: int, chunk_id: str) -> dict[str, Any]:
        """Graph neighbours of a chunk, for the code viewer."""
        callees = self.s.db.query(
            "SELECT DISTINCT e.dst_file AS file_path, e.dst_symbol AS symbol, c.start_line, c.end_line, e.confidence "
            "FROM edges e JOIN chunks c ON c.id = e.dst_chunk_id WHERE e.project_id = ? AND e.kind = 'call' "
            "AND e.src_chunk_id = ? ORDER BY e.confidence DESC LIMIT 12", (project_id, chunk_id))
        callers = self.s.db.query(
            "SELECT DISTINCT e.src_file AS file_path, e.src_symbol AS symbol, c.start_line, c.end_line, e.confidence "
            "FROM edges e JOIN chunks c ON c.id = e.src_chunk_id WHERE e.project_id = ? AND e.kind = 'call' "
            "AND e.dst_chunk_id = ? ORDER BY e.confidence DESC LIMIT 12", (project_id, chunk_id))
        chunk = self.s.db.query_one("SELECT file_path FROM chunks WHERE id = ? AND project_id = ?",
                                    (chunk_id, project_id))
        imports, imported_by = [], []
        if chunk:
            imports = self.s.db.query(
                "SELECT DISTINCT dst_file AS file_path FROM edges WHERE project_id = ? AND kind = 'import' "
                "AND src_file = ? AND dst_file IS NOT NULL AND dst_symbol IS NULL ORDER BY dst_file",
                (project_id, chunk["file_path"]))
            imported_by = self.s.db.query(
                "SELECT DISTINCT src_file AS file_path FROM edges WHERE project_id = ? AND kind = 'import' "
                "AND dst_file = ? AND dst_symbol IS NULL ORDER BY src_file", (project_id, chunk["file_path"]))
        return {"callees": callees, "callers": callers, "imports": imports, "imported_by": imported_by}
