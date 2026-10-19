"""Thumbs up / down feedback on answers, and turning it into evaluation data.

Thumbs-down questions can be added to the project's evaluation set with the file
(and optionally the symbol) that actually answers them. Feedback can be exported as
JSONL training pairs (question, positive chunk, negative chunks) for a future
reranker; no training happens here.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

from ..index.bm25 import BM25Index
from ..util import dumps, loads, utcnow
from ..errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from ..services import Services


class FeedbackService:
    def __init__(self, services: "Services"):
        self.s = services

    # ----- feedback records --------------------------------------------------------------------
    def submit(self, message_id: int, rating: int, comment: str | None = None) -> dict[str, Any]:
        if rating not in (-1, 1):
            raise InvalidInputError("rating must be 1 (thumbs up) or -1 (thumbs down)")
        message = self.s.db.query_one(
            "SELECT * FROM chat_messages WHERE id = ? AND role = 'assistant'", (message_id,))
        if message is None:
            raise NotFoundError("Answer not found")
        sources = loads(message["retrieved_json"], []) or []
        citations = loads(message["citations_json"], []) or []
        now = utcnow()
        comment = (comment or "").strip()[:2000] or None
        self.s.db.execute(
            "INSERT INTO feedback (project_id, message_id, learner, question, answer, retrieved_chunk_ids_json, "
            "cited_chunk_ids_json, rating, comment, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(message_id) DO UPDATE SET rating = excluded.rating, comment = excluded.comment, "
            "updated_at = excluded.updated_at",
            (message["project_id"], message_id, message["learner"], message["question"] or "", message["content"],
             dumps([s["chunk_id"] for s in sources]), dumps([c["chunk_id"] for c in citations]), rating, comment,
             now, now),
        )
        return self.get_by_message(message_id)  # type: ignore[return-value]

    def get_by_message(self, message_id: int) -> dict[str, Any] | None:
        row = self.s.db.query_one("SELECT * FROM feedback WHERE message_id = ?", (message_id,))
        return self._view(row) if row else None

    def _view(self, row: dict[str, Any]) -> dict[str, Any]:
        eval_row = self.s.db.query_one("SELECT id, expected_file, expected_symbol FROM eval_questions "
                                       "WHERE feedback_id = ?", (row["id"],))
        return {
            "id": row["id"], "project_id": row["project_id"], "message_id": row["message_id"],
            "learner": row["learner"], "question": row["question"], "answer": row["answer"],
            "retrieved_chunk_ids": loads(row["retrieved_chunk_ids_json"], []),
            "cited_chunk_ids": loads(row["cited_chunk_ids_json"], []), "rating": row["rating"],
            "comment": row["comment"], "created_at": row["created_at"], "updated_at": row["updated_at"],
            "eval_question": eval_row,
        }

    def list(self, project_id: int, rating: int | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM feedback WHERE project_id = ?"
        params: list[Any] = [project_id]
        if rating in (-1, 1):
            sql += " AND rating = ?"
            params.append(rating)
        return [self._view(r) for r in self.s.db.query(sql + " ORDER BY created_at DESC, id DESC", params)]

    def stats(self, project_id: int) -> dict[str, Any]:
        rows = self.s.db.query(
            "SELECT substr(created_at, 1, 10) AS day, SUM(rating = 1) AS up, SUM(rating = -1) AS down "
            "FROM feedback WHERE project_id = ? GROUP BY day ORDER BY day", (project_id,))
        up = sum(r["up"] for r in rows)
        down = sum(r["down"] for r in rows)
        answers = self.s.db.scalar(
            "SELECT COUNT(*) FROM chat_messages WHERE project_id = ? AND role = 'assistant'", (project_id,)) or 0
        return {
            "total": up + down, "up": up, "down": down, "answers": answers,
            "satisfaction": round(up / (up + down), 3) if up + down else None,
            "by_day": [{"day": r["day"], "up": r["up"], "down": r["down"]} for r in rows],
        }

    # ----- evaluation set curation -------------------------------------------------------------
    def add_to_eval(self, feedback_id: int, expected_file: str, expected_symbol: str | None = None) -> dict[str, Any]:
        feedback = self.s.db.query_one("SELECT * FROM feedback WHERE id = ?", (feedback_id,))
        if feedback is None:
            raise NotFoundError("Feedback not found")
        project_id = feedback["project_id"]
        if not self.s.db.query_one("SELECT 1 FROM files WHERE project_id = ? AND path = ?",
                                   (project_id, expected_file)):
            raise InvalidInputError(f"{expected_file} is not an indexed file of this project")
        expected_symbol = (expected_symbol or "").strip() or None
        if expected_symbol and not self.s.db.query_one(
            "SELECT 1 FROM symbols WHERE project_id = ? AND file_path = ? AND (qualified_name = ? OR name = ?)",
            (project_id, expected_file, expected_symbol, expected_symbol),
        ):
            raise InvalidInputError(f"Symbol {expected_symbol} was not found in {expected_file}")
        existing = self.s.db.query_one("SELECT id FROM eval_questions WHERE feedback_id = ?", (feedback_id,))
        if existing:
            self.s.db.execute("UPDATE eval_questions SET expected_file = ?, expected_symbol = ? WHERE id = ?",
                              (expected_file, expected_symbol, existing["id"]))
            question_id = existing["id"]
        else:
            question_id = self.s.db.execute(
                "INSERT INTO eval_questions (project_id, question, expected_file, expected_symbol, feedback_id, "
                "created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (project_id, feedback["question"], expected_file, expected_symbol, feedback_id, utcnow()),
            )
        return self.s.db.query_one("SELECT * FROM eval_questions WHERE id = ?", (question_id,))  # type: ignore

    def eval_questions(self, project_id: int) -> list[dict[str, Any]]:
        return self.s.db.query("SELECT * FROM eval_questions WHERE project_id = ? ORDER BY id", (project_id,))

    def delete_eval_question(self, question_id: int) -> bool:
        with self.s.db.tx() as conn:
            return conn.execute("DELETE FROM eval_questions WHERE id = ?", (question_id,)).rowcount > 0

    # ----- training-pair export ----------------------------------------------------------------
    def _chunks(self, ids: list[str]) -> dict[str, dict[str, Any]]:
        """Chunk rows by id (SQLite only, so exports work without opening the vector store)."""
        wanted = list(dict.fromkeys(i for i in ids if i))
        rows: list[dict[str, Any]] = []
        for start in range(0, len(wanted), 500):
            batch = wanted[start:start + 500]
            rows += self.s.db.query(f"SELECT * FROM chunks WHERE id IN ({','.join('?' * len(batch))})", batch)
        return {r["id"]: r for r in rows}

    @staticmethod
    def _chunk_view(row: dict[str, Any]) -> dict[str, Any]:
        return {"chunk_id": row["id"], "file_path": row["file_path"], "start_line": row["start_line"],
                "end_line": row["end_line"], "symbol": row["symbol"], "text": row["content"]}

    def _positive_for_correction(self, project_id: int, question: str, expected_file: str,
                                 expected_symbol: str | None) -> dict[str, Any] | None:
        if expected_symbol:
            row = self.s.db.query_one(
                "SELECT chunk_id FROM symbols WHERE project_id = ? AND file_path = ? AND "
                "(qualified_name = ? OR name = ?) AND chunk_id IS NOT NULL ORDER BY (qualified_name = ?) DESC LIMIT 1",
                (project_id, expected_file, expected_symbol, expected_symbol, expected_symbol),
            )
            if row:
                return self._chunks([row["chunk_id"]]).get(row["chunk_id"])
        rows = self.s.db.query("SELECT * FROM chunks WHERE project_id = ? AND file_path = ? ORDER BY start_line",
                               (project_id, expected_file))
        if not rows:
            return None
        ranked = BM25Index([r["id"] for r in rows], [r["content"] for r in rows]).search(question, 1)
        best = ranked[0][0] if ranked else rows[0]["id"]
        return next(r for r in rows if r["id"] == best)

    def training_pairs(self, project_id: int | None = None, max_negatives: int = 7) -> Iterator[dict[str, Any]]:
        """Yield (question, positive, negatives) records built from feedback."""
        sql = ("SELECT f.*, e.expected_file, e.expected_symbol FROM feedback f "
               "LEFT JOIN eval_questions e ON e.feedback_id = f.id")
        params: tuple = ()
        if project_id is not None:
            sql += " WHERE f.project_id = ?"
            params = (project_id,)
        for row in self.s.db.query(sql + " ORDER BY f.id", params):
            retrieved = loads(row["retrieved_chunk_ids_json"], []) or []
            cited = loads(row["cited_chunk_ids_json"], []) or []
            base = {"query": row["question"], "feedback_id": row["id"], "project_id": row["project_id"],
                    "rating": row["rating"], "comment": row["comment"], "created_at": row["created_at"]}
            if row["rating"] == -1 and row["expected_file"]:
                positive = self._positive_for_correction(row["project_id"], row["question"], row["expected_file"],
                                                         row["expected_symbol"])
                if positive is None:
                    continue
                chunks = self._chunks(retrieved)
                negatives = [chunks[cid] for cid in retrieved
                             if cid in chunks and chunks[cid]["file_path"] != row["expected_file"]]
                yield {**base, "label_source": "thumbs_down_corrected", "positive": self._chunk_view(positive),
                       "negatives": [self._chunk_view(c) for c in negatives[:max_negatives]]}
            elif row["rating"] == 1 and cited:
                chunks = self._chunks(retrieved + cited)
                negatives = [chunks[cid] for cid in retrieved if cid in chunks and cid not in cited]
                for cid in cited:
                    if cid in chunks:
                        yield {**base, "label_source": "thumbs_up_cited", "positive": self._chunk_view(chunks[cid]),
                               "negatives": [self._chunk_view(c) for c in negatives[:max_negatives]]}

    def export_jsonl(self, path: Path, project_id: int | None = None) -> dict[str, Any]:
        counts: dict[str, int] = defaultdict(int)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in self.training_pairs(project_id):
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                counts[record["label_source"]] += 1
        total_feedback = self.s.db.scalar(
            "SELECT COUNT(*) FROM feedback" + (" WHERE project_id = ?" if project_id is not None else ""),
            (project_id,) if project_id is not None else ()) or 0
        return {"path": str(path), "pairs": sum(counts.values()), "by_source": dict(counts),
                "feedback_records": total_feedback}
