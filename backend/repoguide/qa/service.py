"""Answer questions about a project with streaming output and file/line citations.

Every answer is checked after generation: a draft that cites no excerpt (and does not say
the code lacks the answer) is rewritten once with a stricter instruction. Answers that still
cite nothing are marked unverified, and file paths the answer mentions that are not in the
index are reported as warnings.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator

from ..config import Settings
from ..db import Database
from ..llm.ollama import ChatLLM, OllamaError
from ..retrieval.hybrid import HybridRetriever, RetrievalResult
from ..util import dumps, loads, truncate, utcnow
from .citations import (NOT_FOUND_MESSAGE, ContextBlock, align_citations, build_context, format_context,
                        is_not_found, parse_citations, unknown_paths)

log = logging.getLogger(__name__)

SYSTEM_PROMPT = f"""You are RepoGuide, an assistant that explains a software project using ONLY the numbered code excerpts the user provides.

Rules:
1. Answer only from the excerpts. Never use outside knowledge about this project and never invent files, functions, settings or behaviour.
2. Cite the excerpts that support each statement with their number in square brackets right after the statement, e.g. [1] or [2][4]. Only use numbers that appear in the excerpts.
3. If the excerpts do not contain the answer, reply exactly "{NOT_FOUND_MESSAGE}" and then, in one sentence, name the closest related code you did see (with its citation), if any. Do not guess.
4. Refer to code by file path and symbol name. Start with a direct answer, then give details or steps. Use Markdown and keep code snippets short and copied from the excerpts."""

# Appended to the question: asking for the supporting lines first keeps a small model on the excerpts.
EVIDENCE_INSTRUCTION = (
    "Before answering, find the excerpt lines that answer the question. Base every statement on those lines and "
    "cite their excerpt number like [1]. If no excerpt answers it, say you could not find it."
)
# Used for the single rewrite of a draft that cited nothing. The draft itself is not shown again,
# so the model is not anchored to an unsupported answer.
REWRITE_INSTRUCTION = (
    "Important: an answer without excerpt citations is rejected. End every statement with the number of the "
    "excerpt that supports it, e.g. [1], and state only what the excerpts show. If they do not answer the "
    f"question, reply exactly \"{NOT_FOUND_MESSAGE}\""
)

# grounded column: 1 = answer cites the code, 0 = model said the code does not contain the answer,
# -1 = answer cites nothing even after the rewrite (unverified), NULL = generation failed.
CITED, NOT_FOUND, UNVERIFIED = 1, 0, -1

_FOLLOW_UP = re.compile(r"\b(it|its|this|that|these|those|they|them|there|above|previous|same|also)\b", re.I)
_MARKERS = re.compile(r"\[\d+(?:\s*[,–-]\s*\d+)*\]")


class QAService:
    def __init__(self, settings: Settings, db: Database, retriever: HybridRetriever, llm: ChatLLM):
        self.settings = settings
        self.db = db
        self.retriever = retriever
        self.llm = llm

    # ----- history ----------------------------------------------------------------------------
    def history(self, project_id: int, learner: str | None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT m.*, f.rating AS feedback_rating, f.comment AS feedback_comment FROM chat_messages m "
            "LEFT JOIN feedback f ON f.message_id = m.id "
            "WHERE m.project_id = ? AND IFNULL(m.learner, '') = IFNULL(?, '') ORDER BY m.id DESC LIMIT ?",
            (project_id, learner or None, limit),
        )
        rows.reverse()
        return [self._message_view(r) for r in rows]

    @staticmethod
    def _message_view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"], "role": row["role"], "content": row["content"], "question": row.get("question"),
            "learner": row.get("learner"), "citations": loads(row.get("citations_json"), []),
            "sources": loads(row.get("retrieved_json"), []), "grounded": row.get("grounded"),
            "warnings": loads(row.get("warnings_json"), []) or [],
            "created_at": row["created_at"], "feedback": (
                {"rating": row["feedback_rating"], "comment": row.get("feedback_comment")}
                if row.get("feedback_rating") is not None else None
            ),
        }

    def clear_history(self, project_id: int, learner: str | None) -> int:
        with self.db.tx() as conn:
            cur = conn.execute("DELETE FROM chat_messages WHERE project_id = ? AND IFNULL(learner, '') = IFNULL(?, '')",
                               (project_id, learner or None))
            return cur.rowcount

    def _recent_turns(self, project_id: int, learner: str | None) -> list[tuple[str, str]]:
        turns = self.settings.chat_history_turns
        if turns <= 0:
            return []
        rows = self.db.query(
            "SELECT question, content FROM chat_messages WHERE project_id = ? AND role = 'assistant' "
            "AND IFNULL(learner, '') = IFNULL(?, '') AND question IS NOT NULL ORDER BY id DESC LIMIT ?",
            (project_id, learner or None, turns),
        )
        rows.reverse()
        return [(r["question"], r["content"]) for r in rows]

    # ----- answering --------------------------------------------------------------------------
    def retrieval_query(self, question: str, turns: list[tuple[str, str]]) -> str:
        """Short or pronoun-heavy follow-ups are retrieved together with the previous question."""
        if turns and (len(question.split()) <= 6 or _FOLLOW_UP.search(question)):
            return f"{turns[-1][0]}\n{question}"
        return question

    def build_messages(self, question: str, blocks: list[ContextBlock],
                       turns: list[tuple[str, str]]) -> list[dict[str, str]]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for previous_q, previous_a in turns:
            messages.append({"role": "user", "content": previous_q})
            messages.append({"role": "assistant", "content": truncate(_MARKERS.sub("", previous_a), 800)})
        messages.append({
            "role": "user",
            "content": f"Code excerpts:\n\n{format_context(blocks)}\n\nQuestion: {question}\n\n{EVIDENCE_INSTRUCTION}",
        })
        return messages

    def prepare(self, project_id: int, question: str, learner: str | None = None,
                use_history: bool = True) -> tuple[RetrievalResult, list[ContextBlock], list[dict[str, str]]]:
        turns = self._recent_turns(project_id, learner) if use_history else []
        result = self.retriever.retrieve(project_id, self.retrieval_query(question, turns))
        blocks = build_context(result.chunks, self.settings.context_char_budget)
        return result, blocks, self.build_messages(question, blocks, turns)

    @staticmethod
    def needs_rewrite(answer: str, blocks: list[ContextBlock]) -> bool:
        citations, _ = parse_citations(answer, blocks)
        return bool(blocks) and not citations and not is_not_found(answer)

    @staticmethod
    def rewrite_messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        last = messages[-1]
        return [*messages[:-1], {"role": last["role"], "content": f"{last['content']}\n\n{REWRITE_INSTRUCTION}"}]

    def answer_stream(self, project_id: int, question: str, learner: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield events: sources -> token* -> [rewrite -> token*] -> done (or error)."""
        question = question.strip()
        user_id = self.db.execute(
            "INSERT INTO chat_messages (project_id, learner, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
            (project_id, learner or None, question, utcnow()),
        )
        blocks: list[ContextBlock] = []
        parts: list[str] = []
        try:
            result, blocks, messages = self.prepare(project_id, question, learner)
            yield {"type": "sources", "user_message_id": user_id, "sources": [b.source() for b in blocks],
                   "timings_ms": result.timings_ms}
            if not blocks:
                parts.append(NOT_FOUND_MESSAGE + " The project has no indexed code that matches the question.")
                yield {"type": "token", "text": parts[0]}
            else:
                options = {"temperature": self.settings.qa_temperature, "num_predict": self.settings.qa_max_tokens}
                for piece in self.llm.stream(messages, **options):
                    parts.append(piece)
                    yield {"type": "token", "text": piece}
                if self.needs_rewrite("".join(parts), blocks):
                    yield {"type": "rewrite",
                           "message": "The first draft cited no code, so it is being rewritten from the excerpts."}
                    parts = []
                    for piece in self.llm.stream(self.rewrite_messages(messages), **options):
                        parts.append(piece)
                        yield {"type": "token", "text": piece}
        except OllamaError as exc:
            log.warning("Answer generation failed: %s", exc)
            message_id = self._save_answer(project_id, learner, question, "".join(parts), blocks, error=str(exc))
            yield {"type": "error", "message": str(exc), "message_id": message_id}
            return
        except Exception as exc:  # unexpected failure: report it to the UI instead of a broken stream
            log.exception("Answer generation failed")
            message_id = self._save_answer(project_id, learner, question, "".join(parts), blocks, error=str(exc))
            yield {"type": "error", "message": f"Internal error: {exc}", "message_id": message_id}
            return
        answer = "".join(parts).strip()
        message_id = self._save_answer(project_id, learner, question, answer, blocks)
        row = self.db.query_one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
        yield {"type": "done", "message": self._message_view(row or {})}

    def run_once(self, project_id: int, question: str) -> dict[str, Any]:
        """Answer without chat history and without saving anything (used by the evaluation harness)."""
        result, blocks, messages = self.prepare(project_id, question, use_history=False)
        rewritten = False
        if blocks:
            options = {"temperature": self.settings.qa_temperature, "num_predict": self.settings.qa_max_tokens}
            answer = self.llm.complete(messages, **options).strip()
            if self.needs_rewrite(answer, blocks):
                rewritten = True
                answer = self.llm.complete(self.rewrite_messages(messages), **options).strip()
        else:
            answer = NOT_FOUND_MESSAGE
        answer, corrections = align_citations(answer, blocks)
        citations, invalid = parse_citations(answer, blocks)
        return {"answer": answer, "citations": citations, "invalid_markers": invalid, "blocks": blocks,
                "retrieval": result, "not_found": is_not_found(answer), "rewritten": rewritten,
                "citation_corrections": corrections}

    def answer(self, project_id: int, question: str, learner: str | None = None) -> dict[str, Any]:
        """Non-streaming convenience wrapper (used by tests and scripts)."""
        final: dict[str, Any] = {}
        for event in self.answer_stream(project_id, question, learner):
            if event["type"] in ("done", "error"):
                final = event
        if final.get("type") == "error":
            raise OllamaError(final["message"])
        return final["message"]

    def _known_paths(self, project_id: int) -> list[str]:
        return [r["path"] for r in self.db.query("SELECT path FROM files WHERE project_id = ?", (project_id,))]

    def _save_answer(self, project_id: int, learner: str | None, question: str, answer: str,
                     blocks: list[ContextBlock], error: str | None = None) -> int:
        corrections: list[str] = []
        if not error:
            answer, corrections = align_citations(answer, blocks)
        citations, invalid = parse_citations(answer, blocks)
        warnings: list[str] = []
        if error:
            answer = (answer + "\n\n" if answer else "") + f"⚠️ The answer could not be completed: {error}"
            grounded = None
        elif is_not_found(answer):
            grounded = NOT_FOUND
        elif citations:
            grounded = CITED
        else:
            grounded = UNVERIFIED
            warnings.append("This answer does not cite any code. Check it against the source before relying on it.")
        for path in unknown_paths(answer, self._known_paths(project_id)):
            warnings.append(f"Mentions `{path}`, which is not a file in this project's index.")
        if invalid:
            warnings.append(f"Cites excerpt number(s) {', '.join(map(str, invalid))} that were not provided.")
        warnings += corrections
        sources =[{k: v for k, v in b.source().items() if k != "content"} for b in blocks]
        return self.db.execute(
            "INSERT INTO chat_messages (project_id, learner, role, content, question, citations_json, retrieved_json, "
            "grounded, warnings_json, created_at) VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
            (project_id, learner or None, answer, question, dumps(citations), dumps(sources), grounded,
             dumps(warnings), utcnow()),
        )
