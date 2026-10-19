"""Generate and cache the project overview.

Tech stack, entry points, the module map and the diagram are computed from the index;
the local LLM writes the plain-language summary, module descriptions and execution flow.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from ..jobs import JobReporter
from ..llm.ollama import OllamaError
from ..util import dumps, loads, truncate, utcnow
from . import facts as facts_mod
from .mermaid import build_mermaid
from ..errors import NotFoundError

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "modules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "description": {"type": "string"}},
                "required": ["path", "description"],
            },
        },
    },
    "required": ["summary", "modules"],
}

FLOW_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "files": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "description", "files"],
            },
        }
    },
    "required": ["steps"],
}

GROUNDING = ("Use only the information given. Do not invent files, features or technologies that are not listed. "
             "If something is unclear, say less rather than guessing.")


class OverviewService:
    def __init__(self, services: "Services"):
        self.s = services

    # ----- cache ---------------------------------------------------------------------------------
    def get(self, project_id: int) -> dict[str, Any] | None:
        row = self.s.db.query_one("SELECT * FROM overviews WHERE project_id = ?", (project_id,))
        if row is None:
            return None
        version = self.s.db.scalar("SELECT index_version FROM projects WHERE id = ?", (project_id,)) or 0
        content = loads(row["content_json"], {})
        content["stale"] = row["index_version"] < version
        content["generated_at"] = row["generated_at"]
        return content

    def is_current(self, project_id: int) -> bool:
        overview = self.get(project_id)
        return bool(overview) and not overview.get("stale") and not overview.get("warnings")

    # ----- generation ----------------------------------------------------------------------------
    def generate(self, project_id: int, reporter: JobReporter | None = None) -> dict[str, Any]:
        project = self.s.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            raise NotFoundError(f"Project {project_id} does not exist")

        def progress(step: int, message: str) -> None:
            if reporter:
                reporter.update(phase="overview", current=step, total=4, message=message)

        progress(0, "Collecting project facts")
        collector = facts_mod.FactCollector(self.s.db, project_id, Path(project["root_path"]))
        manifests = collector.manifests()
        tech_stack = collector.tech_stack(manifests)
        entry_points = collector.entry_points(manifests)
        modules, depth = collector.modules()
        readme_path, readme = collector.readme()
        languages = collector.languages()
        warnings: list[str] = []
        # If the model is unavailable, keep the last good text instead of replacing it with nothing.
        previous = self.get(project_id) or {}

        progress(1, "Writing the project summary")
        summary, descriptions = "", {}
        if not collector.files:
            # With no files the model only has the project name to go on, and it invents a project from it.
            summary = ("No supported source files were found in this project, so there is nothing to summarise. "
                       "Check that the folder contains code in a supported language, then re-index.")
        else:
            try:
                summary, descriptions = self._summarise(project, readme, tech_stack, entry_points, modules)
            except OllamaError as exc:
                summary = previous.get("summary") or ""
                descriptions = {m["path"]: m["description"] for m in previous.get("modules", [])
                                if m.get("description")}
                warnings.append(f"Summary could not be generated: {exc}"
                                + (" Showing the previous summary, which may be out of date." if summary else ""))

        progress(2, "Tracing the main execution flow")
        flow: list[dict[str, Any]] = []
        try:
            flow = self._execution_flow(project_id, entry_points, summary)
        except OllamaError as exc:
            known = {f["path"] for f in collector.files}
            flow = [{**step, "files": [f for f in step.get("files", []) if f in known]}
                    for step in previous.get("execution_flow", [])]
            warnings.append(f"Execution flow could not be generated: {exc}"
                            + (" Showing the previous flow, which may be out of date." if flow else ""))

        progress(3, "Drawing the architecture diagram")
        external_usage: dict[str, set[str]] = {}
        file_module = {f["path"]: m["path"] for m in modules for f in m["files"]}
        for row in self.s.db.query(
            "SELECT DISTINCT src_file, external FROM edges WHERE project_id = ? AND kind = 'import' "
            "AND external IS NOT NULL", (project_id,),
        ):
            known = facts_mod._lookup_pkg(row["external"])
            if known and row["src_file"] in file_module:
                external_usage.setdefault(known[0], set()).add(file_module[row["src_file"]])
        mermaid = build_mermaid(modules, descriptions, [e["file"] for e in entry_points], tech_stack,
                                external_usage, collector.http_links(modules))
        for module in modules:
            module["description"] = descriptions.get(module["path"], "")

        content = {
            "project": {"name": project["name"], "files": len(collector.files),
                        "lines": sum(f["line_count"] for f in collector.files),
                        "chunks": project["chunk_count"], "module_depth": depth},
            "summary": summary,
            "readme_path": readme_path,
            "languages": languages,
            "tech_stack": tech_stack,
            "entry_points": entry_points,
            "modules": modules,
            "execution_flow": flow,
            "mermaid": mermaid,
            "graph": collector.graph_stats(),
            "model": getattr(self.s.llm, "model", "unknown"),
            "warnings": warnings,
        }
        version = self.s.db.scalar("SELECT index_version FROM projects WHERE id = ?", (project_id,)) or 0
        self.s.db.execute(
            "INSERT INTO overviews (project_id, index_version, content_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(project_id) DO UPDATE SET index_version = excluded.index_version, "
            "content_json = excluded.content_json, generated_at = excluded.generated_at",
            (project_id, version, dumps(content), utcnow()),
        )
        progress(4, "Overview ready" if not warnings else "Overview saved with warnings")
        return {"warnings": warnings, "modules": len(modules), "flow_steps": len(flow)}

    # ----- LLM steps ------------------------------------------------------------------------------
    def _summarise(self, project: dict[str, Any], readme: str, tech_stack: list[dict[str, Any]],
                   entry_points: list[dict[str, Any]], modules: list[dict[str, Any]]) -> tuple[str, dict[str, str]]:
        module_lines = []
        for module in modules[:30]:
            files = ", ".join(PurePosixPath(f["path"]).name for f in module["files"][:8])
            symbols = "; ".join(
                f"{s['kind']} {s['name']}" + (f" ({truncate(s['doc'], 70)})" if s["doc"] else "")
                for s in module["key_symbols"][:6]
            )
            module_lines.append(f"- {module['path']}: files [{files}]; symbols: {symbols or 'none'}")
        stack = ", ".join(f"{t['name']} ({t['category']})" for t in tech_stack) or "unknown"
        entries = "\n".join(f"- {e['file']}: {e['reason']}" for e in entry_points) or "- none detected"
        # The project name is deliberately left out: it is a label the user typed, and with little code
        # to go on the model invents a purpose from it. A real name reaches the model through the README.
        prompt = (
            f"README excerpt:\n{readme or '(no README)'}\n\n"
            f"Detected tech stack: {stack}\n\nEntry points:\n{entries}\n\n"
            f"Modules (folders) with their files and main symbols:\n" + "\n".join(module_lines) + "\n\n"
            "Write JSON with:\n"
            "- summary: 3-5 plain-language sentences for a new developer: what the project does, who uses it, "
            "and how it is structured.\n"
            "- modules: for EVERY module path listed above, one sentence describing what lives there. Use the "
            "module path exactly as written (a folder, not a file, and no trailing slash).\n" + GROUNDING
        )
        valid_paths = [m["path"] for m in modules[:30]]
        # Constrain module paths to the real ones; small models otherwise add trailing slashes or file names.
        schema = json.loads(json.dumps(SUMMARY_SCHEMA))
        if valid_paths:
            schema["properties"]["modules"]["items"]["properties"]["path"]["enum"] = valid_paths
        data = self.s.llm.json(
            [{"role": "system", "content": "You document software projects for new team members."},
             {"role": "user", "content": prompt}],
            schema,
        )
        descriptions: dict[str, str] = {}
        file_notes: dict[str, list[str]] = {}
        file_module = {f["path"]: m["path"] for m in modules for f in m["files"]}
        for item in (data or {}).get("modules", []) or []:
            if not isinstance(item, dict) or not item.get("description"):
                continue
            path = str(item.get("path", "")).strip().strip("/").removeprefix("./")
            text = truncate(str(item["description"]).strip(), 300)
            if path in valid_paths:
                descriptions.setdefault(path, text)
            elif path in file_module:  # a file-level description: attribute it to the file's module
                file_notes.setdefault(file_module[path], []).append(f"{PurePosixPath(path).name}: {text}")
        for module_path, notes in file_notes.items():
            descriptions.setdefault(module_path, truncate(" ".join(notes[:2]), 300))
        return str((data or {}).get("summary", "")).strip(), descriptions

    def _call_chains(self, project_id: int, start_chunks: list[str], max_edges: int = 24) -> list[str]:
        """Breadth-first walk over call edges from the entry chunks (best-effort call graph)."""
        lines: list[str] = []
        seen = set(start_chunks)
        queue = deque((cid, 0) for cid in start_chunks)
        while queue and len(lines) < max_edges:
            chunk_id, depth = queue.popleft()
            if depth >= 3:
                continue
            for edge in self.s.db.query(
                "SELECT src_file, src_symbol, dst_file, dst_symbol, dst_chunk_id FROM edges WHERE project_id = ? "
                "AND kind = 'call' AND src_chunk_id = ? AND confidence >= 0.5 ORDER BY line",
                (project_id, chunk_id),
            ):
                lines.append(f"{'  ' * depth}{edge['src_file']}:{edge['src_symbol'] or '<module>'} -> "
                             f"{edge['dst_file']}:{edge['dst_symbol']}")
                if edge["dst_chunk_id"] not in seen:
                    seen.add(edge["dst_chunk_id"])
                    queue.append((edge["dst_chunk_id"], depth + 1))
        return lines

    def _execution_flow(self, project_id: int, entry_points: list[dict[str, Any]], summary: str) -> list[dict[str, Any]]:
        if not entry_points:
            return []
        excerpts: list[str] = []
        chain_lines: list[str] = []
        budget = 9000
        for entry in entry_points[:3]:
            chunks = self.s.db.query(
                "SELECT id, file_path, start_line, end_line, symbol, content, language FROM chunks "
                "WHERE project_id = ? AND file_path = ? ORDER BY start_line", (project_id, entry["file"]),
            )
            if not chunks:
                continue
            # Prefer chunks that actually call into other code (e.g. the __main__ block or app factory).
            calling = {r["src_chunk_id"] for r in self.s.db.query(
                "SELECT DISTINCT src_chunk_id FROM edges WHERE project_id = ? AND kind = 'call' AND src_file = ?",
                (project_id, entry["file"]))}
            start = [c for c in chunks if c["id"] in calling] or chunks[:1]
            chain_lines += self._call_chains(project_id, [c["id"] for c in start])
            for chunk in start[:3]:
                block = f"{chunk['file_path']}:{chunk['start_line']}-{chunk['end_line']}\n{chunk['content']}"
                if len(block) < budget:
                    excerpts.append(block)
                    budget -= len(block)
        if not excerpts:
            return []
        callee_ids = []
        for line in chain_lines[:12]:
            dst_file, dst_symbol = line.strip().split(" -> ")[1].rsplit(":", 1)
            row = self.s.db.query_one(
                "SELECT c.file_path, c.start_line, c.end_line, c.content FROM chunks c "
                "JOIN symbols s ON s.chunk_id = c.id "
                "WHERE s.project_id = ? AND s.file_path = ? AND s.qualified_name = ? LIMIT 1",
                (project_id, dst_file, dst_symbol),
            )
            if row and row["file_path"] + str(row["start_line"]) not in callee_ids:
                callee_ids.append(row["file_path"] + str(row["start_line"]))
                block = f"{row['file_path']}:{row['start_line']}-{row['end_line']}\n{row['content']}"
                if len(block) < budget:
                    excerpts.append(block)
                    budget -= len(block)
        prompt = (
            (f"Project summary: {summary}\n\n" if summary else "")
            + "Entry points:\n" + "\n".join(f"- {e['file']}: {e['reason']}" for e in entry_points[:5])
            + "\n\nCall chains found by static analysis (caller -> callee):\n"
            + ("\n".join(chain_lines) or "(none)")
            + "\n\nCode excerpts:\n\n" + "\n\n".join(f"```\n{x}\n```" for x in excerpts)
            + "\n\nDescribe the main execution flow of this project as 3 to 7 ordered steps, starting from the "
              "entry point (for example: startup, configuration, request handling, core logic, storage, response). "
              "For each step give a short title, one or two sentences of description, and the file paths involved "
              "(only paths shown above). " + GROUNDING
        )
        data = self.s.llm.json(
            [{"role": "system", "content": "You explain how software projects run, step by step."},
             {"role": "user", "content": prompt}],
            FLOW_SCHEMA,
        )
        known = {r["path"] for r in self.s.db.query("SELECT path FROM files WHERE project_id = ?", (project_id,))}
        steps = []
        for item in (data or {}).get("steps", []) or []:
            if not isinstance(item, dict) or not item.get("title"):
                continue
            files = [f for f in (item.get("files") or []) if isinstance(f, str) and f in known]
            steps.append({"title": truncate(str(item["title"]).strip(), 120),
                          "description": truncate(str(item.get("description", "")).strip(), 600),
                          "files": files[:6]})
        return steps[:8]
