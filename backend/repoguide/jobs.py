"""Background jobs (indexing, overview, onboarding, evaluation) with progress stored in SQLite."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable

from .db import Database
from .util import dumps, loads, utcnow

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")


class JobReporter:
    """Progress handle passed to job functions; writes are throttled."""

    def __init__(self, db: Database, job_id: int, min_interval: float = 0.25):
        self.db = db
        self.job_id = job_id
        self.min_interval = min_interval
        self._last_write = 0.0
        self._state: dict[str, Any] = {}

    def update(self, phase: str | None = None, current: int | None = None, total: int | None = None,
               message: str | None = None, force: bool = False) -> None:
        if phase is not None and phase != self._state.get("phase"):
            force = True
            self._state["current"] = 0
            self._state["total"] = 0
        for key, value in (("phase", phase), ("current", current), ("total", total), ("message", message)):
            if value is not None:
                self._state[key] = value
        now = time.monotonic()
        if not force and now - self._last_write < self.min_interval:
            return
        self._last_write = now
        self.flush()

    def flush(self) -> None:
        if not self._state:
            return
        columns = ", ".join(f"{k} = ?" for k in self._state)
        self.db.execute(f"UPDATE jobs SET {columns} WHERE id = ?", (*self._state.values(), self.job_id))


class PrintReporter:
    """Same interface as JobReporter, for command-line scripts: prints phase changes and progress."""

    def __init__(self, stream=None):
        import sys

        self.stream = stream or sys.stdout
        self._phase: str | None = None
        self._last = 0.0

    def update(self, phase: str | None = None, current: int | None = None, total: int | None = None,
               message: str | None = None, force: bool = False) -> None:
        now = time.monotonic()
        if phase and phase != self._phase:
            self._phase = phase
            force = True
        if not force and now - self._last < 2.0:
            return
        self._last = now
        counter = f" {current}/{total}" if total else ""
        print(f"[{self._phase or '-'}]{counter} {message or ''}".rstrip(), file=self.stream, flush=True)

    def flush(self) -> None:
        pass


JobFunction = Callable[[JobReporter], Any]


class JobManager:
    def __init__(self, db: Database, max_workers: int = 2):
        self.db = db
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="repoguide-job")
        self._lock = threading.Lock()
        self._futures: dict[int, Future] = {}

    def recover_interrupted(self) -> None:
        """Run once when the server starts: no job can still be running, so release what a crash left behind.

        Only the server may call this; a script sharing the database must not fail the server's live jobs.
        """
        self.db.execute(
            "UPDATE jobs SET status = 'error', error = 'Interrupted because the server restarted.', "
            "finished_at = ? WHERE status IN ('queued', 'running')",
            (utcnow(),),
        )
        # An index job killed mid-run would otherwise leave its project showing 'indexing' forever.
        self.db.execute(
            "UPDATE projects SET status = 'error', error = 'Indexing was interrupted because the server "
            "restarted. Re-index to finish.', updated_at = ? WHERE status = 'indexing'",
            (utcnow(),),
        )

    def submit(self, kind: str, fn: JobFunction, project_id: int | None = None, key: str | None = None) -> dict:
        """Start a job unless an identical one is already queued or running."""
        with self._lock:
            existing = self.db.query_one(
                "SELECT id FROM jobs WHERE kind = ? AND IFNULL(project_id, 0) = IFNULL(?, 0) "
                "AND IFNULL(job_key, '') = IFNULL(?, '') AND status IN ('queued', 'running') "
                "ORDER BY id DESC LIMIT 1",
                (kind, project_id, key),
            )
            if existing:
                return self.get(existing["id"])  # type: ignore[return-value]
            job_id = self.db.execute(
                "INSERT INTO jobs (project_id, kind, job_key, status, phase, created_at) "
                "VALUES (?, ?, ?, 'queued', 'queued', ?)",
                (project_id, kind, key, utcnow()),
            )
            self._futures[job_id] = self._executor.submit(self._run, job_id, fn)
        return self.get(job_id)  # type: ignore[return-value]

    def _run(self, job_id: int, fn: JobFunction) -> None:
        reporter = JobReporter(self.db, job_id)
        self.db.execute("UPDATE jobs SET status = 'running', phase = 'starting', started_at = ? WHERE id = ?",
                        (utcnow(), job_id))
        try:
            result = fn(reporter)
            reporter.flush()
            self.db.execute(
                "UPDATE jobs SET status = 'done', phase = 'done', result_json = ?, finished_at = ? WHERE id = ?",
                (dumps(result) if result is not None else None, utcnow(), job_id),
            )
        except Exception as exc:  # the job's error is reported to the UI, not raised
            log.exception("Job %s failed", job_id)
            reporter.flush()
            self.db.execute(
                "UPDATE jobs SET status = 'error', error = ?, finished_at = ? WHERE id = ?",
                (str(exc)[:2000] or exc.__class__.__name__, utcnow(), job_id),
            )

    def get(self, job_id: int) -> dict | None:
        row = self.db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if row is None:
            return None
        row["result"] = loads(row.pop("result_json"), None)
        return row

    def latest(self, project_id: int, kind: str | None = None, key: str | None = None) -> dict | None:
        sql = "SELECT id FROM jobs WHERE project_id = ?"
        params: list[Any] = [project_id]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if key is not None:
            sql += " AND IFNULL(job_key, '') = ?"
            params.append(key)
        row = self.db.query_one(sql + " ORDER BY id DESC LIMIT 1", params)
        return self.get(row["id"]) if row else None

    def active(self, project_id: int | None = None) -> list[dict]:
        if project_id is None:
            rows = self.db.query("SELECT id FROM jobs WHERE status IN ('queued', 'running') ORDER BY id")
        else:
            rows = self.db.query(
                "SELECT id FROM jobs WHERE project_id = ? AND status IN ('queued', 'running') ORDER BY id",
                (project_id,),
            )
        return [j for j in (self.get(r["id"]) for r in rows) if j]

    def wait(self, job_id: int, timeout: float | None = None) -> dict | None:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.get(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
