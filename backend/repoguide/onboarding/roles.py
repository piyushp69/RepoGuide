"""Which files matter for which role, scored from paths, file types, imports and the code graph."""

from __future__ import annotations

from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from ..db import Database

ROLES: dict[str, dict[str, str]] = {
    "frontend": {
        "label": "Frontend",
        "focus": "UI components and pages, client-side state, styling, and how the UI calls the backend API",
    },
    "backend": {
        "label": "Backend",
        "focus": "API endpoints, business logic, data models, persistence, authentication and configuration",
    },
    "data_ml": {
        "label": "Data / ML",
        "focus": "data loading and processing, models, training or inference pipelines, analysis and data storage",
    },
    "fullstack": {
        "label": "Full-stack",
        "focus": "the end-to-end flow from the UI through the API to business logic and storage",
    },
}

FRONTEND_EXT = {".tsx", ".jsx", ".vue", ".svelte", ".css", ".scss", ".sass", ".less", ".html"}
FRONTEND_DIRS = {"frontend", "client", "web", "ui", "components", "pages", "views", "public", "styles", "hooks",
                 "store", "stores", "layouts", "widgets", "screens"}
FRONTEND_PKGS = {"react", "react-dom", "vue", "svelte", "@angular/core", "next", "solid-js", "preact", "react-router-dom"}
BACKEND_EXT = {".py", ".go", ".java", ".rb", ".php", ".rs", ".cs", ".kt", ".sql"}
BACKEND_DIRS = {"backend", "server", "api", "routes", "routers", "controllers", "services", "models", "db",
                "database", "migrations", "handlers", "middleware", "auth", "repositories", "schemas", "core"}
BACKEND_PKGS = {"fastapi", "flask", "django", "express", "koa", "fastify", "@nestjs/core", "sqlalchemy", "starlette",
                "aiohttp", "prisma", "@prisma/client", "mongoose", "sequelize", "typeorm", "pg", "sqlite3",
                "psycopg2", "psycopg", "celery", "redis", "itsdangerous", "pymongo"}
DATA_EXT = {".ipynb", ".sql", ".r", ".jl"}
DATA_DIRS = {"data", "ml", "notebooks", "pipelines", "pipeline", "etl", "training", "features", "analysis",
             "experiments", "datasets", "inference", "eda"}
DATA_PKGS = {"pandas", "numpy", "sklearn", "scikit-learn", "torch", "tensorflow", "keras", "xgboost", "lightgbm",
             "scipy", "matplotlib", "seaborn", "transformers", "datasets", "polars", "pyspark", "statsmodels"}
DOC_NAMES = ("readme", "contributing", "architecture", "overview")
CONFIG_NAMES = {"package.json", "pyproject.toml", "requirements.txt", "setup.py", "config.py", "settings.py",
                "vite.config.ts", "vite.config.js", "tsconfig.json", "docker-compose.yml", "dockerfile", ".env.example"}


def _signals(path: str, language: str, externals: set[str]) -> dict[str, float]:
    p = PurePosixPath(path)
    dirs = {d.lower() for d in p.parts[:-1]}
    ext = p.suffix.lower()
    fe = (2 if ext in FRONTEND_EXT else 0) + (2 if dirs & FRONTEND_DIRS else 0) + (3 if externals & FRONTEND_PKGS else 0)
    be = (1 if ext in BACKEND_EXT else 0) + (2 if dirs & BACKEND_DIRS else 0) + (3 if externals & BACKEND_PKGS else 0)
    data = (2 if ext in DATA_EXT else 0) + (2 if dirs & DATA_DIRS else 0) + (3 if externals & DATA_PKGS else 0)
    if language in ("javascript", "typescript") and not fe and "src" in dirs and not dirs & BACKEND_DIRS:
        fe = 1  # a JS/TS file under src/ outside any backend folder is most likely UI code
    return {"frontend": fe, "backend": be, "data_ml": data}


def candidate_files(db: Database, project_id: int, role: str, entry_files: list[str], limit: int = 16) -> tuple[list[dict[str, Any]], int]:
    """Rank files for a role. Returns (candidates, number of files that clearly match the role)."""
    files = db.query("SELECT path, language, line_count FROM files WHERE project_id = ?", (project_id,))
    externals: dict[str, set[str]] = {}
    for row in db.query("SELECT src_file, external FROM edges WHERE project_id = ? AND kind = 'import' "
                        "AND external IS NOT NULL", (project_id,)):
        externals.setdefault(row["src_file"], set()).add(row["external"].lower())
    indegree = Counter()
    for row in db.query("SELECT dst_file, COUNT(DISTINCT src_file) AS n FROM edges WHERE project_id = ? "
                        "AND dst_file IS NOT NULL AND dst_file != src_file GROUP BY dst_file", (project_id,)):
        indegree[row["dst_file"]] = row["n"]
    symbols: dict[str, list[str]] = {}
    for row in db.query("SELECT file_path, qualified_name, kind FROM symbols WHERE project_id = ? AND parent IS NULL "
                        "ORDER BY start_line", (project_id,)):
        symbols.setdefault(row["file_path"], []).append(f"{row['kind']} {row['qualified_name']}")

    scored = []
    role_matches = 0
    for f in files:
        path, name = f["path"], PurePosixPath(f["path"]).name.lower()
        signals = _signals(path, f["language"], externals.get(path, set()))
        if role == "fullstack":
            base = max(signals["frontend"], signals["backend"]) + 0.5 * min(signals["frontend"], signals["backend"])
        else:
            base = signals[role]
        is_doc = name.startswith(DOC_NAMES) and f["language"] in ("markdown", "rst", "text")
        is_test = "test" in path.lower() or "spec." in name
        is_config = name in CONFIG_NAMES
        if base > 0 and not is_doc and not is_config:
            role_matches += 1
        score = base * 3 + min(indegree[path], 5) + (4 if path in entry_files else 0)
        score += 6 if is_doc and path.count("/") == 0 else 0
        score += 1 if is_config and role in ("backend", "fullstack", "data_ml") else 0
        score -= 3 if is_test else 0
        if f["language"] in ("markdown", "text", "json", "yaml", "toml") and not (is_doc or is_config):
            score -= 2
        notes = []
        if path in entry_files:
            notes.append("entry point")
        if indegree[path]:
            notes.append(f"imported by {indegree[path]} file(s)")
        if is_test:
            notes.append("tests")
        scored.append({"path": path, "language": f["language"], "lines": f["line_count"], "score": score,
                       "symbols": symbols.get(path, [])[:6], "notes": notes, "role_signal": base})
    scored.sort(key=lambda c: (-c["score"], c["path"]))
    return [c for c in scored if c["score"] > 0][:limit], role_matches
