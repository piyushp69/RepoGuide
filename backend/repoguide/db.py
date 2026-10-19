"""SQLite storage for application data (projects, chunks, graph, chat, onboarding, feedback).

Vectors live in ChromaDB; everything else lives here. Each call opens a short-lived
connection, which keeps the code safe to use from request threads and background jobs.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('local', 'github')),
    source TEXT NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    branch TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    embed_model TEXT,
    index_version INTEGER NOT NULL DEFAULT 0,
    file_count INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    skipped_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_indexed_at TEXT
);

CREATE TABLE IF NOT EXISTS files (
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    hash TEXT NOT NULL,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    line_count INTEGER NOT NULL,
    parse_json TEXT NOT NULL,
    indexed_at TEXT NOT NULL,
    PRIMARY KEY (project_id, path)
);

CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    symbol TEXT,
    symbol_type TEXT NOT NULL,
    language TEXT NOT NULL,
    content TEXT NOT NULL,
    ordinal INTEGER NOT NULL DEFAULT 0,
    embedded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chunks_project_file ON chunks(project_id, file_path);
CREATE INDEX IF NOT EXISTS ix_chunks_project_embedded ON chunks(project_id, embedded);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    kind TEXT NOT NULL,
    parent TEXT,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    chunk_id TEXT,
    docstring TEXT
);
CREATE INDEX IF NOT EXISTS ix_symbols_project_name ON symbols(project_id, name);
CREATE INDEX IF NOT EXISTS ix_symbols_project_file ON symbols(project_id, file_path);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('import', 'call')),
    src_file TEXT NOT NULL,
    src_symbol TEXT,
    src_chunk_id TEXT,
    dst_file TEXT,
    dst_symbol TEXT,
    dst_chunk_id TEXT,
    external TEXT,
    line INTEGER,
    confidence REAL NOT NULL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS ix_edges_src_chunk ON edges(project_id, src_chunk_id);
CREATE INDEX IF NOT EXISTS ix_edges_dst_chunk ON edges(project_id, dst_chunk_id);
CREATE INDEX IF NOT EXISTS ix_edges_src_file ON edges(project_id, src_file);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    job_key TEXT,
    status TEXT NOT NULL,
    phase TEXT,
    current INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    result_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_jobs_project ON jobs(project_id, kind, status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    learner TEXT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    question TEXT,
    citations_json TEXT,
    retrieved_json TEXT,
    grounded INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_chat_project ON chat_messages(project_id, id);

CREATE TABLE IF NOT EXISTS overviews (
    project_id INTEGER PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    index_version INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_paths (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content_json TEXT NOT NULL,
    index_version INTEGER NOT NULL,
    generated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_paths_project_role ON learning_paths(project_id, role, id);

CREATE TABLE IF NOT EXISTS quizzes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    UNIQUE (path_id, step_index)
);

CREATE TABLE IF NOT EXISTS step_progress (
    learner_id INTEGER NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
    path_id INTEGER NOT NULL REFERENCES learning_paths(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY (learner_id, path_id, step_index)
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id INTEGER NOT NULL REFERENCES learners(id) ON DELETE CASCADE,
    quiz_id INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    answers_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    message_id INTEGER UNIQUE REFERENCES chat_messages(id) ON DELETE SET NULL,
    learner TEXT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    retrieved_chunk_ids_json TEXT NOT NULL,
    cited_chunk_ids_json TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_feedback_project ON feedback(project_id, created_at);

CREATE TABLE IF NOT EXISTS eval_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    expected_file TEXT NOT NULL,
    expected_symbol TEXT,
    feedback_id INTEGER REFERENCES feedback(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL
);
"""


# Columns added after the first release: (table, column, definition). Applied on startup if missing.
MIGRATIONS = [
    ("chat_messages", "warnings_json", "TEXT"),
]


class Database:
    """Thin helper around sqlite3 with per-call connections."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for table, column, definition in MIGRATIONS:
                existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Run several statements in one transaction."""
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        conn = self.connect()
        try:
            return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]
        finally:
            conn.close()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        conn = self.connect()
        try:
            row = conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        conn = self.connect()
        try:
            row = conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def execute(self, sql: str, params: Iterable[Any] = ()) -> int:
        """Execute one statement and return ``lastrowid``."""
        with self.tx() as conn:
            cur = conn.execute(sql, tuple(params))
            return int(cur.lastrowid or 0)
