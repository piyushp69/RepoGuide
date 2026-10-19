"""Deterministic facts about a project, derived from manifests, the index and the code graph."""

from __future__ import annotations

import json
import posixpath
import re
import sys
import tomllib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from ..db import Database
from ..ingest.languages import TREE_SITTER_LANGUAGES
from ..util import loads

STDLIB = set(getattr(sys, "stdlib_module_names", ()))
NON_CODE_LANGUAGES = {"markdown", "rst", "text", "json", "yaml", "toml", "ini", "dotenv", "pip-requirements", "xml"}

# package name -> (display name, category)
KNOWN_PACKAGES: dict[str, tuple[str, str]] = {
    # Python web / backend
    "fastapi": ("FastAPI", "Web framework"), "flask": ("Flask", "Web framework"), "django": ("Django", "Web framework"),
    "starlette": ("Starlette", "Web framework"), "aiohttp": ("aiohttp", "Web framework"),
    "tornado": ("Tornado", "Web framework"), "uvicorn": ("Uvicorn", "Server"), "gunicorn": ("Gunicorn", "Server"),
    "sqlalchemy": ("SQLAlchemy", "Database"), "alembic": ("Alembic", "Database"), "psycopg2": ("PostgreSQL (psycopg2)", "Database"),
    "psycopg": ("PostgreSQL (psycopg)", "Database"), "pymongo": ("MongoDB", "Database"), "redis": ("Redis", "Database"),
    "peewee": ("Peewee", "Database"), "sqlite3": ("SQLite", "Database"), "chromadb": ("ChromaDB", "Database"),
    "celery": ("Celery", "Background jobs"), "pydantic": ("Pydantic", "Validation"), "marshmallow": ("Marshmallow", "Validation"),
    "requests": ("Requests", "HTTP client"), "httpx": ("HTTPX", "HTTP client"), "itsdangerous": ("itsdangerous", "Security"),
    "jinja2": ("Jinja2", "Templating"), "click": ("Click", "CLI"), "typer": ("Typer", "CLI"), "boto3": ("AWS SDK (boto3)", "Cloud"),
    # Python data / ML
    "pandas": ("pandas", "Data / ML"), "numpy": ("NumPy", "Data / ML"), "scipy": ("SciPy", "Data / ML"),
    "sklearn": ("scikit-learn", "Data / ML"), "scikit-learn": ("scikit-learn", "Data / ML"), "torch": ("PyTorch", "Data / ML"),
    "tensorflow": ("TensorFlow", "Data / ML"), "keras": ("Keras", "Data / ML"), "transformers": ("Hugging Face Transformers", "Data / ML"),
    "xgboost": ("XGBoost", "Data / ML"), "lightgbm": ("LightGBM", "Data / ML"), "matplotlib": ("Matplotlib", "Data / ML"),
    "seaborn": ("seaborn", "Data / ML"), "plotly": ("Plotly", "Data / ML"), "jupyter": ("Jupyter", "Data / ML"),
    "langchain": ("LangChain", "Data / ML"), "openai": ("OpenAI SDK", "Data / ML"), "ollama": ("Ollama", "Data / ML"),
    "streamlit": ("Streamlit", "Web framework"), "gradio": ("Gradio", "Web framework"),
    "pytest": ("pytest", "Testing"), "unittest": ("unittest", "Testing"), "hypothesis": ("Hypothesis", "Testing"),
    "tree_sitter": ("tree-sitter", "Parsing"), "tree-sitter": ("tree-sitter", "Parsing"),
    # JavaScript / TypeScript
    "react": ("React", "Frontend framework"), "react-dom": ("React DOM", "Frontend framework"), "next": ("Next.js", "Frontend framework"),
    "vue": ("Vue", "Frontend framework"), "nuxt": ("Nuxt", "Frontend framework"), "svelte": ("Svelte", "Frontend framework"),
    "@angular/core": ("Angular", "Frontend framework"), "solid-js": ("SolidJS", "Frontend framework"),
    "react-native": ("React Native", "Frontend framework"), "electron": ("Electron", "Frontend framework"),
    "express": ("Express", "Web framework"), "koa": ("Koa", "Web framework"), "fastify": ("Fastify", "Web framework"),
    "@nestjs/core": ("NestJS", "Web framework"), "hono": ("Hono", "Web framework"),
    "vite": ("Vite", "Build tooling"), "webpack": ("webpack", "Build tooling"), "typescript": ("TypeScript", "Build tooling"),
    "esbuild": ("esbuild", "Build tooling"), "tailwindcss": ("Tailwind CSS", "Styling"), "sass": ("Sass", "Styling"),
    "styled-components": ("styled-components", "Styling"), "@mui/material": ("Material UI", "Styling"),
    "redux": ("Redux", "State management"), "@reduxjs/toolkit": ("Redux Toolkit", "State management"),
    "zustand": ("Zustand", "State management"), "@tanstack/react-query": ("TanStack Query", "State management"),
    "react-router-dom": ("React Router", "Frontend framework"), "react-router": ("React Router", "Frontend framework"),
    "axios": ("Axios", "HTTP client"), "prisma": ("Prisma", "Database"), "@prisma/client": ("Prisma", "Database"),
    "mongoose": ("Mongoose", "Database"), "sequelize": ("Sequelize", "Database"), "typeorm": ("TypeORM", "Database"),
    "pg": ("PostgreSQL (pg)", "Database"), "mysql2": ("MySQL", "Database"), "knex": ("Knex", "Database"),
    "jest": ("Jest", "Testing"), "vitest": ("Vitest", "Testing"), "mocha": ("Mocha", "Testing"), "cypress": ("Cypress", "Testing"),
    "@playwright/test": ("Playwright", "Testing"), "@testing-library/react": ("Testing Library", "Testing"),
    "eslint": ("ESLint", "Build tooling"), "prettier": ("Prettier", "Build tooling"), "graphql": ("GraphQL", "API"),
    "@apollo/client": ("Apollo Client", "API"), "socket.io": ("Socket.IO", "API"), "mermaid": ("Mermaid", "Frontend framework"),
}
CATEGORY_ORDER = ["Language", "Frontend framework", "Web framework", "Server", "Database", "Data / ML", "API",
                  "State management", "HTTP client", "Validation", "Security", "Background jobs", "Templating",
                  "Styling", "Parsing", "CLI", "Cloud", "Testing", "Build tooling", "Infrastructure", "Other"]
LANGUAGE_NAMES = {"python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "go": "Go", "rust": "Rust",
                  "java": "Java", "kotlin": "Kotlin", "csharp": "C#", "ruby": "Ruby", "php": "PHP", "cpp": "C++", "c": "C",
                  "swift": "Swift", "scala": "Scala", "dart": "Dart", "vue": "Vue SFC", "svelte": "Svelte",
                  "html": "HTML", "css": "CSS", "scss": "SCSS", "sql": "SQL", "shell": "Shell"}
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-\[\]]+)")


def _norm_pkg(name: str) -> str:
    return re.sub(r"\[.*\]", "", name).strip().lower().replace("_", "-")


def _lookup_pkg(name: str) -> tuple[str, str] | None:
    lowered = name.lower()
    return KNOWN_PACKAGES.get(lowered) or KNOWN_PACKAGES.get(lowered.replace("-", "_")) \
        or KNOWN_PACKAGES.get(lowered.replace("_", "-"))


def _parse_manifest(path: str, text: str) -> tuple[list[str], dict[str, Any]]:
    """Return (dependency names, extra info such as scripts/entry points) for one manifest file."""
    name = PurePosixPath(path).name.lower()
    deps: list[str] = []
    extra: dict[str, Any] = {}
    try:
        if name == "package.json":
            data = json.loads(text)
            for key in ("dependencies", "devDependencies", "peerDependencies"):
                deps += list((data.get(key) or {}).keys())
            extra = {"scripts": data.get("scripts") or {}, "main": data.get("main"), "bin": data.get("bin"),
                     "name": data.get("name"), "description": data.get("description")}
        elif name.startswith("requirements") and name.endswith(".txt"):
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith(("-", "git+", "http")):
                    continue
                match = _REQ_LINE.match(line)
                if match:
                    deps.append(_norm_pkg(match.group(1)))
        elif name == "pyproject.toml":
            data = tomllib.loads(text)
            project = data.get("project") or {}
            for spec in project.get("dependencies") or []:
                match = _REQ_LINE.match(spec)
                if match:
                    deps.append(_norm_pkg(match.group(1)))
            for group in (project.get("optional-dependencies") or {}).values():
                for spec in group:
                    match = _REQ_LINE.match(spec)
                    if match:
                        deps.append(_norm_pkg(match.group(1)))
            poetry = ((data.get("tool") or {}).get("poetry") or {})
            deps += [_norm_pkg(k) for k in (poetry.get("dependencies") or {}) if k.lower() != "python"]
            extra = {"scripts": project.get("scripts") or poetry.get("scripts") or {},
                     "name": project.get("name") or poetry.get("name"),
                     "description": project.get("description") or poetry.get("description")}
        elif name == "pipfile":
            data = tomllib.loads(text)
            deps += [_norm_pkg(k) for k in (data.get("packages") or {})]
            deps += [_norm_pkg(k) for k in (data.get("dev-packages") or {})]
        elif name == "setup.py":
            block = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.DOTALL)
            if block:
                deps += [_norm_pkg(m) for m in re.findall(r"['\"]([A-Za-z0-9_.\-\[\]]+)", block.group(1))]
        elif name == "go.mod":
            deps += re.findall(r"^\s*(?:require\s+)?([\w.\-/]+)\s+v[\d.]+", text, re.MULTILINE)
        elif name == "cargo.toml":
            data = tomllib.loads(text)
            deps += list((data.get("dependencies") or {}).keys())
        elif name == "gemfile":
            deps += re.findall(r"^\s*gem\s+['\"]([\w\-]+)", text, re.MULTILINE)
        elif name == "composer.json":
            deps += list((json.loads(text).get("require") or {}).keys())
        elif name == "pom.xml":
            deps += re.findall(r"<artifactId>([\w.\-]+)</artifactId>", text)
    except (ValueError, tomllib.TOMLDecodeError, AttributeError):
        pass
    return deps, extra


MANIFEST_NAMES = {"package.json", "pyproject.toml", "pipfile", "setup.py", "go.mod", "cargo.toml", "gemfile",
                  "composer.json", "pom.xml"}


def _is_manifest(path: str) -> bool:
    name = PurePosixPath(path).name.lower()
    return name in MANIFEST_NAMES or (name.startswith("requirements") and name.endswith(".txt"))


def module_of(path: str, depth: int) -> str:
    parent = posixpath.dirname(path)
    if not parent:
        return "(root)"
    return "/".join(parent.split("/")[:depth])


def choose_module_depth(code_paths: list[str], max_modules: int = 24) -> int:
    depth = 3
    while depth > 1 and len({module_of(p, depth) for p in code_paths}) > max_modules:
        depth -= 1
    return depth


class FactCollector:
    def __init__(self, db: Database, project_id: int, root: Path):
        self.db = db
        self.project_id = project_id
        self.root = root
        self.files = db.query(
            "SELECT path, language, line_count, size, parse_json FROM files WHERE project_id = ? ORDER BY path",
            (project_id,),
        )
        self.payloads = {f["path"]: loads(f["parse_json"], {}) or {} for f in self.files}

    def read(self, path: str, limit: int = 200_000) -> str:
        try:
            return (self.root / path).read_text(encoding="utf-8", errors="replace")[:limit]
        except OSError:
            return ""

    # ----- languages & stack ----------------------------------------------------------------------
    def languages(self) -> list[dict[str, Any]]:
        files, lines = Counter(), Counter()
        for f in self.files:
            files[f["language"]] += 1
            lines[f["language"]] += f["line_count"]
        return [{"language": lang, "files": files[lang], "lines": lines[lang]}
                for lang in sorted(files, key=lambda l: (-lines[l], l))]

    def manifests(self) -> dict[str, dict[str, Any]]:
        found = {}
        for f in self.files:
            if _is_manifest(f["path"]):
                deps, extra = _parse_manifest(f["path"], self.read(f["path"]))
                found[f["path"]] = {"dependencies": deps, **extra}
        return found

    def external_imports(self) -> Counter:
        counts: Counter = Counter()
        for row in self.db.query(
            "SELECT external, COUNT(DISTINCT src_file) AS n FROM edges WHERE project_id = ? AND kind = 'import' "
            "AND external IS NOT NULL GROUP BY external", (self.project_id,),
        ):
            counts[row["external"]] = row["n"]
        return counts

    def tech_stack(self, manifests: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        stack: dict[str, dict[str, Any]] = {}

        def add(name: str, category: str, evidence: str) -> None:
            entry = stack.setdefault(name, {"name": name, "category": category, "evidence": []})
            if evidence not in entry["evidence"] and len(entry["evidence"]) < 3:
                entry["evidence"].append(evidence)

        for lang in self.languages():
            if lang["language"] in LANGUAGE_NAMES and lang["files"]:
                add(LANGUAGE_NAMES[lang["language"]], "Language", f"{lang['files']} files, {lang['lines']} lines")
        for path, info in manifests.items():
            for dep in info["dependencies"]:
                known = _lookup_pkg(dep)
                if known:
                    add(known[0], known[1], f"dependency in {path}")
        for module, count in self.external_imports().most_common():
            known = _lookup_pkg(module)
            if known:
                add(known[0], known[1], f"imported in {count} file{'s' if count != 1 else ''}")
            elif module in ("sqlite3",):
                add("SQLite", "Database", f"imported in {count} files")
        names = {PurePosixPath(f["path"]).name.lower() for f in self.files}
        if any(n.startswith("dockerfile") for n in names) or "docker-compose.yml" in names or "compose.yaml" in names:
            add("Docker", "Infrastructure", "Dockerfile / compose file")
        if any(f["path"].startswith(".github/workflows/") for f in self.files):
            add("GitHub Actions", "Infrastructure", ".github/workflows")
        order = {c: i for i, c in enumerate(CATEGORY_ORDER)}
        return sorted(stack.values(), key=lambda e: (order.get(e["category"], 99), e["name"].lower()))

    # ----- entry points -----------------------------------------------------------------------------
    def entry_points(self, manifests: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}

        def add(path: str, reason: str, weight: int, line: int | None = None) -> None:
            entry = found.setdefault(path, {"file": path, "reasons": [], "weight": 0, "line": line})
            if reason not in entry["reasons"]:
                entry["reasons"].append(reason)
                entry["weight"] += weight
            if line and not entry.get("line"):
                entry["line"] = line

        paths = {f["path"] for f in self.files}
        for f in self.files:
            path, payload = f["path"], self.payloads.get(f["path"], {})
            name = PurePosixPath(path).name.lower()
            if payload.get("has_main_guard"):
                add(path, "runs as a script (`if __name__ == \"__main__\"`)", 5)
            if name in ("main.py", "app.py", "manage.py", "wsgi.py", "asgi.py", "__main__.py", "server.py", "cli.py",
                        "run.py"):
                add(path, f"conventional entry-point file name ({name})", 2)
            if name in ("main.ts", "main.tsx", "main.js", "main.jsx", "index.tsx", "index.jsx", "server.ts",
                        "server.js", "app.js", "app.ts") and posixpath.dirname(path).split("/")[-1] in ("src", "", "app",
                                                                                                         "server"):
                add(path, f"conventional entry-point file name ({name})", 2)
        patterns = [
            ("%FastAPI(%", "creates the FastAPI application", 4),
            ("%Flask(__name__%", "creates the Flask application", 4),
            ("%express()%", "creates the Express server", 4),
            ("%.listen(%", "starts listening for HTTP requests", 2),
            ("%createRoot(%", "mounts the React app into the page", 4),
            ("%ReactDOM.render(%", "mounts the React app into the page", 4),
            ("%createApp(%", "creates the frontend application", 3),
            ("%uvicorn.run(%", "starts the ASGI server", 3),
            ("%argparse.ArgumentParser(%", "defines a command-line interface", 2),
            ("%@click.command%", "defines a command-line interface", 2),
        ]
        for pattern, reason, weight in patterns:
            for row in self.db.query(
                "SELECT file_path, start_line, content FROM chunks WHERE project_id = ? AND content LIKE ? "
                "AND language IN ('python', 'javascript', 'typescript') ORDER BY file_path, start_line LIMIT 20",
                (self.project_id, pattern),
            ):
                needle = pattern.strip("%")
                offset = next((i for i, ln in enumerate(row["content"].split("\n")) if needle in ln), 0)
                add(row["file_path"], reason, weight, row["start_line"] + offset)
        for manifest, info in manifests.items():
            base = posixpath.dirname(manifest)
            for key in ("main",):
                target = info.get(key)
                if isinstance(target, str):
                    candidate = posixpath.normpath(posixpath.join(base, target))
                    if candidate in paths:
                        add(candidate, f"`{key}` in {manifest}", 3)
            for script_name in ("start", "dev", "serve"):
                command = (info.get("scripts") or {}).get(script_name)
                if isinstance(command, str):
                    for token in command.split():
                        candidate = posixpath.normpath(posixpath.join(base, token))
                        if candidate in paths:
                            add(candidate, f"`{script_name}` script in {manifest}", 3)
        ranked = sorted(found.values(), key=lambda e: (-e["weight"], e["file"]))
        return [{"file": e["file"], "line": e.get("line"), "reason": "; ".join(e["reasons"])} for e in ranked[:8]]

    # ----- modules ------------------------------------------------------------------------------------
    def modules(self) -> tuple[list[dict[str, Any]], int]:
        code_files = [f for f in self.files if f["language"] not in NON_CODE_LANGUAGES]
        depth = choose_module_depth([f["path"] for f in code_files] or [f["path"] for f in self.files])
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for f in code_files:
            grouped[module_of(f["path"], depth)].append(f)
        symbols = self.db.query(
            "SELECT file_path, name, qualified_name, kind, start_line, docstring FROM symbols "
            "WHERE project_id = ? AND parent IS NULL ORDER BY file_path, start_line", (self.project_id,),
        )
        inbound = Counter()
        for row in self.db.query(
            "SELECT dst_symbol, dst_file, COUNT(*) AS n FROM edges WHERE project_id = ? AND kind = 'call' "
            "GROUP BY dst_file, dst_symbol", (self.project_id,),
        ):
            inbound[(row["dst_file"], row["dst_symbol"])] = row["n"]
        by_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sym in symbols:
            by_file[sym["file_path"]].append(sym)
        module_edges: Counter = Counter()
        for row in self.db.query(
            "SELECT DISTINCT src_file, dst_file FROM edges WHERE project_id = ? AND kind = 'import' "
            "AND dst_file IS NOT NULL", (self.project_id,),
        ):
            src, dst = module_of(row["src_file"], depth), module_of(row["dst_file"], depth)
            if src != dst and src in grouped and dst in grouped:
                module_edges[(src, dst)] += 1
        modules = []
        for path in sorted(grouped):
            files = sorted(grouped[path], key=lambda f: -f["line_count"])
            module_symbols = [s for f in files for s in by_file.get(f["path"], [])]
            module_symbols.sort(key=lambda s: (-inbound[(s["file_path"], s["qualified_name"])],
                                               s["kind"] != "class", s["file_path"], s["start_line"]))
            modules.append({
                "path": path,
                "files": [{"path": f["path"], "language": f["language"], "lines": f["line_count"]} for f in files],
                "lines": sum(f["line_count"] for f in files),
                "key_symbols": [{"name": s["qualified_name"], "kind": s["kind"], "file": s["file_path"],
                                 "line": s["start_line"], "doc": (s["docstring"] or "")[:160]}
                                for s in module_symbols[:8]],
                "depends_on": sorted({dst for (src, dst) in module_edges if src == path}),
                "used_by": sorted({src for (src, dst) in module_edges if dst == path}),
            })
        return modules, depth

    def readme(self, limit: int = 3000) -> tuple[str | None, str]:
        candidates = sorted((f["path"] for f in self.files if PurePosixPath(f["path"]).name.lower().startswith("readme")),
                            key=lambda p: (p.count("/"), len(p)))
        if not candidates:
            return None, ""
        return candidates[0], self.read(candidates[0])[:limit]

    def http_links(self, modules: list[dict[str, Any]]) -> list[tuple[str, str]]:
        """(frontend module, backend module) pairs that talk over HTTP, detected from fetch calls and routes."""
        callers = {r["file_path"] for r in self.db.query(
            "SELECT DISTINCT file_path FROM chunks WHERE project_id = ? AND language IN ('javascript', 'typescript') "
            "AND (content LIKE '%fetch(%' OR content LIKE '%axios.%') AND (content LIKE '%/api%' OR content LIKE '%http%')",
            (self.project_id,))}
        servers = {r["file_path"] for r in self.db.query(
            "SELECT DISTINCT file_path FROM chunks WHERE project_id = ? AND (content LIKE '%@app.%' OR content LIKE "
            "'%@router.%' OR content LIKE '%Blueprint(%' OR content LIKE '%APIRouter(%' OR content LIKE '%app.get(%' OR "
            "content LIKE '%router.get(%' OR content LIKE '%@api.%')", (self.project_id,))}
        file_module = {f["path"]: m["path"] for m in modules for f in m["files"]}
        pairs = {(file_module[c], file_module[s]) for c in callers for s in servers
                 if c in file_module and s in file_module and file_module[c] != file_module[s]}
        return sorted(pairs)

    def graph_stats(self) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT SUM(kind = 'import' AND dst_file IS NOT NULL) AS internal_imports, "
            "SUM(kind = 'import' AND external IS NOT NULL) AS external_imports, SUM(kind = 'call') AS calls "
            "FROM edges WHERE project_id = ?", (self.project_id,),
        ) or {}
        return {k: int(v or 0) for k, v in row.items()}


def is_code_language(language: str) -> bool:
    return language in TREE_SITTER_LANGUAGES or language not in NON_CODE_LANGUAGES
