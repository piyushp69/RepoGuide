"""Learning paths, grounded quizzes, learner progress and the manager dashboard."""

from __future__ import annotations

import logging
import random
import re
from bisect import bisect_right
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..jobs import JobReporter
from ..llm.ollama import OllamaError
from ..util import dumps, loads, normalize_question, short_hash, truncate, utcnow
from .roles import ROLES, candidate_files
from ..errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)

PATH_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "overview": {"type": "string"},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "goal": {"type": "string"},
                    "files": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}, "why": {"type": "string"}},
                            "required": ["path", "why"],
                        },
                    },
                },
                "required": ["title", "goal", "files"],
            },
        },
    },
    "required": ["title", "overview", "steps"],
}

QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
                    "answer_index": {"type": "integer"},
                    "explanation": {"type": "string"},
                    "file": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["question", "options", "answer_index", "explanation", "file", "evidence"],
            },
        }
    },
    "required": ["questions"],
}

PASS_RATIO = 0.6
_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


class OnboardingService:
    def __init__(self, services: "Services"):
        self.s = services

    # ----- learners -------------------------------------------------------------------------------
    def learners(self) -> list[dict[str, Any]]:
        return self.s.db.query("SELECT id, name, created_at FROM learners ORDER BY name COLLATE NOCASE")

    def ensure_learner(self, name: str) -> dict[str, Any]:
        name = _norm(name)
        if not name or len(name) > 60:
            raise InvalidInputError("Learner name must be 1-60 characters.")
        row = self.s.db.query_one("SELECT id, name, created_at FROM learners WHERE name = ?", (name,))
        if row:
            return row
        learner_id = self.s.db.execute("INSERT INTO learners (name, created_at) VALUES (?, ?)", (name, utcnow()))
        return {"id": learner_id, "name": name, "created_at": utcnow()}

    # ----- learning paths -------------------------------------------------------------------------
    def latest_path(self, project_id: int, role: str) -> dict[str, Any] | None:
        row = self.s.db.query_one(
            "SELECT * FROM learning_paths WHERE project_id = ? AND role = ? ORDER BY id DESC LIMIT 1",
            (project_id, role),
        )
        if row is None:
            return None
        content = loads(row["content_json"], {})
        version = self.s.db.scalar("SELECT index_version FROM projects WHERE id = ?", (project_id,)) or 0
        quizzes = {r["step_index"]: r["id"] for r in self.s.db.query(
            "SELECT id, step_index FROM quizzes WHERE path_id = ?", (row["id"],))}
        for index, step in enumerate(content.get("steps", [])):
            step["quiz_id"] = quizzes.get(index)
        return {"id": row["id"], "project_id": project_id, "role": role, "generated_at": row["generated_at"],
                "stale": row["index_version"] < version, **content}

    def generate_path(self, project_id: int, role: str, reporter: JobReporter | None = None) -> dict[str, Any]:
        if role not in ROLES:
            raise InvalidInputError(f"Unknown role {role!r}")
        if reporter:
            reporter.update(phase="planning", current=0, total=2, message="Selecting files for the role")
        overview = self.s.overview.get(project_id) or {}
        entry_files = [e["file"] for e in overview.get("entry_points", [])]
        candidates, role_matches = candidate_files(self.s.db, project_id, role, entry_files)
        if not candidates:
            raise InvalidInputError("The project has no indexed files to build a learning path from.")
        info = ROLES[role]
        lines = []
        for c in candidates:
            details = ", ".join(c["symbols"][:5]) or "no top-level symbols"
            notes = f" [{'; '.join(c['notes'])}]" if c["notes"] else ""
            lines.append(f"- {c['path']} ({c['language']}, {c['lines']} lines): {details}{notes}")
        fit_note = "" if role_matches >= 3 else (
            f"\nNote: this project has little code specific to the {info['label']} role, so focus on the files that "
            "are most relevant and say so honestly in the overview.")
        prompt = (
            f"Plan an onboarding path for a new {info['label']} developer joining the project "
            f"'{self._project_name(project_id)}'.\nRole focus: {info['focus']}.\n"
            f"Project summary: {overview.get('summary') or '(not available)'}\n\n"
            f"Candidate files (path, language, size, main symbols, notes):\n" + "\n".join(lines) + fit_note + "\n\n"
            "Create an ordered learning path with 3 to 6 steps. Start with the big picture (README or entry point), "
            "then core data structures, then the main flows that matter for this role, then supporting code. "
            "Each step has a short title, a one-sentence goal, and 1 to 4 files from the candidate list, each with a "
            "one-sentence 'why' that says what the learner will understand from that file. Use every file at most "
            "once and only use paths from the candidate list. Also give the path a title and a 2-sentence overview."
        )
        if reporter:
            reporter.update(current=1, message="Asking the local model to order the path")
        known = {c["path"]: c for c in candidates}
        content = None
        for attempt in range(2):
            data = self.s.llm.json(
                [{"role": "system", "content": "You design practical onboarding plans for software developers."},
                 {"role": "user", "content": prompt}],
                PATH_SCHEMA, temperature=0.2 if attempt == 0 else 0.5,
            )
            content = self._clean_path(data, known)
            if content and len(content["steps"]) >= 2:
                break
        if not content or not content["steps"]:
            raise OllamaError("The model did not produce a usable learning path. Try regenerating.")
        content["role_label"] = info["label"]
        content["role_fit"] = "good" if role_matches >= 3 else "limited"
        version = self.s.db.scalar("SELECT index_version FROM projects WHERE id = ?", (project_id,)) or 0
        path_id = self.s.db.execute(
            "INSERT INTO learning_paths (project_id, role, content_json, index_version, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, role, dumps(content), version, utcnow()),
        )
        if reporter:
            reporter.update(current=2, message="Learning path ready")
        return {"path_id": path_id, "steps": len(content["steps"])}

    def _project_name(self, project_id: int) -> str:
        return self.s.db.scalar("SELECT name FROM projects WHERE id = ?", (project_id,)) or "project"

    def _clean_path(self, data: Any, known: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
        if not isinstance(data, dict):
            return None
        used: set[str] = set()
        steps = []
        for step in data.get("steps") or []:
            if not isinstance(step, dict):
                continue
            files = []
            for item in step.get("files") or []:
                path = item.get("path") if isinstance(item, dict) else None
                if path in known and path not in used:
                    used.add(path)
                    files.append({"path": path, "why": truncate(str(item.get("why", "")).strip(), 300),
                                  "language": known[path]["language"], "lines": known[path]["lines"],
                                  "symbols": known[path]["symbols"]})
            if files:
                steps.append({"title": truncate(str(step.get("title", "")).strip() or f"Step {len(steps) + 1}", 120),
                              "goal": truncate(str(step.get("goal", "")).strip(), 300), "files": files})
        if not steps:
            return None
        return {"title": truncate(str(data.get("title", "")).strip(), 120) or "Onboarding path",
                "overview": truncate(str(data.get("overview", "")).strip(), 600), "steps": steps[:6]}

    # ----- quizzes --------------------------------------------------------------------------------
    def quiz(self, quiz_id: int, include_answers: bool = False) -> dict[str, Any] | None:
        row = self.s.db.query_one("SELECT * FROM quizzes WHERE id = ?", (quiz_id,))
        if row is None:
            return None
        content = loads(row["content_json"], {})
        questions = content.get("questions", [])
        if not include_answers:
            questions = [{k: q[k] for k in ("id", "question", "options", "file")} for q in questions]
        return {"id": row["id"], "path_id": row["path_id"], "step_index": row["step_index"],
                "generated_at": row["generated_at"], "questions": questions}

    def quiz_for_step(self, path_id: int, step_index: int) -> dict[str, Any] | None:
        row = self.s.db.query_one("SELECT id FROM quizzes WHERE path_id = ? AND step_index = ?", (path_id, step_index))
        return self.quiz(row["id"]) if row else None

    def _step_context(self, project_id: int, files: list[str], budget: int = 9000) -> tuple[str, dict[str, str]]:
        root = Path(self.s.db.scalar("SELECT root_path FROM projects WHERE id = ?", (project_id,)) or ".")
        file_text: dict[str, str] = {}
        excerpts: list[str] = []
        per_file = max(1500, budget // max(1, len(files)))
        for path in files:
            try:
                file_text[path] = (root / path).read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
            except OSError:
                continue
            chunks = self.s.db.query(
                "SELECT start_line, end_line, symbol, symbol_type, content FROM chunks WHERE project_id = ? AND "
                "file_path = ? ORDER BY (symbol_type IN ('function', 'method', 'class')) DESC, start_line",
                (project_id, path),
            )
            used = 0
            for chunk in chunks:
                block = f"{path}:{chunk['start_line']}-{chunk['end_line']}\n{chunk['content']}"
                if used + len(block) > per_file and used:
                    continue
                excerpts.append(f"```\n{block}\n```")
                used += len(block)
        return "\n\n".join(excerpts), file_text

    @staticmethod
    def _locate(evidence: str, file_text: str) -> int | None:
        """Line number where the evidence appears (whitespace-insensitive), or None."""
        lines = file_text.split("\n")
        normalised = [_norm(line) for line in lines]
        candidates = [_norm(line) for line in evidence.split("\n") if len(_norm(line)) >= 6]
        for candidate in candidates:
            for number, line in enumerate(normalised, start=1):
                if candidate and candidate in line:
                    return number
        whole = _norm(evidence)
        offsets, position = [], 0
        for line in normalised:
            offsets.append(position)
            position += len(line) + 1
        index = " ".join(normalised).find(whole) if len(whole) >= 10 else -1
        return bisect_right(offsets, index) if index >= 0 else None

    def _clean_quiz(self, data: Any, step_files: list[str], file_text: dict[str, str]) -> list[dict[str, Any]]:
        questions = []
        for item in (data or {}).get("questions", []) if isinstance(data, dict) else []:
            if not isinstance(item, dict):
                continue
            options = [_norm(str(o)) for o in item.get("options") or [] if _norm(str(o))]
            answer = item.get("answer_index")
            if len(options) != 4 or len(set(o.lower() for o in options)) != 4:
                continue
            if not isinstance(answer, int) or not 0 <= answer < 4:
                continue
            evidence = str(item.get("evidence", "")).strip()
            file = item.get("file") if item.get("file") in step_files else None
            search = [file] if file else []
            search += [f for f in step_files if f != file]
            line = None
            for candidate in search:
                if candidate in file_text:
                    line = self._locate(evidence, file_text[candidate])
                    if line:
                        file = candidate
                        break
            if not line:  # evidence could not be found in the code: the question is not grounded
                continue
            question = _norm(str(item.get("question", "")))
            if not question:
                continue
            # Shuffle options so the correct answer is not always in the same position.
            order = list(range(4))
            random.Random(short_hash(question)).shuffle(order)
            questions.append({
                "id": short_hash(question, length=10), "question": question,
                "options": [options[i] for i in order], "answer_index": order.index(answer),
                "explanation": truncate(_norm(str(item.get("explanation", ""))), 500),
                "file": file, "line": line, "evidence": truncate(evidence, 300),
            })
        return questions

    def generate_quiz(self, path_id: int, step_index: int, reporter: JobReporter | None = None) -> dict[str, Any]:
        path_row = self.s.db.query_one("SELECT * FROM learning_paths WHERE id = ?", (path_id,))
        if path_row is None:
            raise NotFoundError("Learning path not found")
        content = loads(path_row["content_json"], {})
        steps = content.get("steps", [])
        if not 0 <= step_index < len(steps):
            raise NotFoundError("Step not found")
        step = steps[step_index]
        step_files = [f["path"] for f in step["files"]]
        if reporter:
            reporter.update(phase="quiz", current=0, total=2, message="Reading the step's code")
        context, file_text = self._step_context(path_row["project_id"], step_files)
        if not context:
            raise InvalidInputError("The files for this step could not be read.")
        role_label = ROLES.get(path_row["role"], {}).get("label", path_row["role"])
        prompt = (
            f"A new {role_label} developer has just studied this onboarding step: \"{step['title']}\" "
            f"({step.get('goal', '')}).\n\nCode they read:\n\n{context}\n\n"
            "Write exactly 3 multiple-choice questions that check they understood what this code does "
            "(behaviour, responsibilities, data flow, error handling). Avoid trivia such as line numbers or "
            "spelling. Each question has exactly 4 distinct options with exactly one correct answer, "
            "answer_index (0-3) pointing to the correct option, a short explanation that refers to the code, "
            "the file path the answer comes from, and evidence: one line copied exactly from that file that "
            "proves the answer."
        )
        questions: list[dict[str, Any]] = []
        for attempt in range(3):
            if reporter:
                reporter.update(current=1, message=f"Generating questions (attempt {attempt + 1})")
            data = self.s.llm.json(
                [{"role": "system", "content": "You write fair, code-grounded quizzes for developer onboarding."},
                 {"role": "user", "content": prompt}],
                QUIZ_SCHEMA, temperature=0.3 if attempt == 0 else 0.6,
            )
            for question in self._clean_quiz(data, step_files, file_text):
                if question["id"] not in {q["id"] for q in questions}:
                    questions.append(question)
            if len(questions) >= 3:
                break
        if len(questions) < 2:
            raise OllamaError("The model could not produce quiz questions grounded in this step's code. "
                              "Try again.")
        questions = questions[:3]
        self.s.db.execute(
            "INSERT INTO quizzes (path_id, step_index, content_json, generated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(path_id, step_index) DO UPDATE SET content_json = excluded.content_json, "
            "generated_at = excluded.generated_at",
            (path_id, step_index, dumps({"questions": questions}), utcnow()),
        )
        quiz_id = self.s.db.scalar("SELECT id FROM quizzes WHERE path_id = ? AND step_index = ?",
                                   (path_id, step_index))
        if reporter:
            reporter.update(current=2, message="Quiz ready")
        return {"quiz_id": quiz_id, "questions": len(questions)}

    def submit_quiz(self, quiz_id: int, learner_name: str, answers: list[int | None]) -> dict[str, Any]:
        quiz = self.quiz(quiz_id, include_answers=True)
        if quiz is None:
            raise NotFoundError("Quiz not found")
        learner = self.ensure_learner(learner_name)
        results = []
        score = 0
        for index, question in enumerate(quiz["questions"]):
            chosen = answers[index] if index < len(answers) else None
            correct = chosen == question["answer_index"]
            score += int(correct)
            results.append({"id": question["id"], "chosen": chosen, "correct": correct,
                            "answer_index": question["answer_index"], "explanation": question["explanation"],
                            "file": question["file"], "line": question.get("line"), "evidence": question["evidence"]})
        total = len(quiz["questions"])
        self.s.db.execute(
            "INSERT INTO quiz_attempts (learner_id, quiz_id, score, total, answers_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (learner["id"], quiz_id, score, total, dumps(answers), utcnow()),
        )
        passed = total > 0 and score / total >= PASS_RATIO
        if passed:
            self.set_step(learner["name"], quiz["path_id"], quiz["step_index"], True)
        return {"score": score, "total": total, "passed": passed, "results": results}

    # ----- progress -------------------------------------------------------------------------------
    def set_step(self, learner_name: str, path_id: int, step_index: int, completed: bool) -> None:
        learner = self.ensure_learner(learner_name)
        if completed:
            self.s.db.execute(
                "INSERT INTO step_progress (learner_id, path_id, step_index, completed_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(learner_id, path_id, step_index) DO NOTHING",
                (learner["id"], path_id, step_index, utcnow()),
            )
        else:
            self.s.db.execute("DELETE FROM step_progress WHERE learner_id = ? AND path_id = ? AND step_index = ?",
                              (learner["id"], path_id, step_index))

    def progress(self, learner_name: str, path_id: int) -> dict[str, Any]:
        learner = self.s.db.query_one("SELECT id FROM learners WHERE name = ?", (_norm(learner_name),))
        if learner is None:
            return {"completed_steps": [], "quiz_scores": {}}
        completed = [r["step_index"] for r in self.s.db.query(
            "SELECT step_index FROM step_progress WHERE learner_id = ? AND path_id = ? ORDER BY step_index",
            (learner["id"], path_id))]
        scores = {}
        for row in self.s.db.query(
            "SELECT q.step_index, MAX(a.score * 1.0 / a.total) AS best, COUNT(*) AS attempts FROM quiz_attempts a "
            "JOIN quizzes q ON q.id = a.quiz_id WHERE a.learner_id = ? AND q.path_id = ? AND a.total > 0 "
            "GROUP BY q.step_index", (learner["id"], path_id),
        ):
            scores[row["step_index"]] = {"best": round(row["best"], 3), "attempts": row["attempts"]}
        return {"completed_steps": completed, "quiz_scores": scores}

    # ----- manager dashboard -------------------------------------------------------------------------
    def dashboard(self, project_id: int | None = None) -> dict[str, Any]:
        params: tuple = (project_id,) if project_id else ()
        project_filter = " AND p.project_id = ?" if project_id else ""
        paths = {r["id"]: r for r in self.s.db.query(
            "SELECT p.id, p.project_id, p.role, p.content_json, pr.name AS project_name FROM learning_paths p "
            f"JOIN projects pr ON pr.id = p.project_id WHERE 1 = 1{project_filter}", params)}
        for path in paths.values():
            path["steps_total"] = len(loads(path.pop("content_json"), {}).get("steps", []))
        learners = {r["id"]: {**r, "paths": {}, "questions_asked": 0, "last_activity": r["created_at"]}
                    for r in self.learners()}

        def touch(learner: dict[str, Any], when: str) -> None:
            if when and when > (learner["last_activity"] or ""):
                learner["last_activity"] = when

        def path_entry(learner: dict[str, Any], path_id: int) -> dict[str, Any]:
            path = paths[path_id]
            return learner["paths"].setdefault(path_id, {
                "path_id": path_id, "project_id": path["project_id"], "project_name": path["project_name"],
                "role": path["role"], "role_label": ROLES.get(path["role"], {}).get("label", path["role"]),
                "steps_total": path["steps_total"], "steps_completed": 0, "quiz_attempts": 0, "scores": [],
            })

        for row in self.s.db.query("SELECT learner_id, path_id, COUNT(*) AS n, MAX(completed_at) AS last "
                                   "FROM step_progress GROUP BY learner_id, path_id"):
            if row["path_id"] in paths and row["learner_id"] in learners:
                learner = learners[row["learner_id"]]
                path_entry(learner, row["path_id"])["steps_completed"] = row["n"]
                touch(learner, row["last"])
        for row in self.s.db.query(
            "SELECT a.learner_id, q.path_id, q.step_index, a.score, a.total, a.created_at FROM quiz_attempts a "
            "JOIN quizzes q ON q.id = a.quiz_id ORDER BY a.id"):
            if row["path_id"] in paths and row["learner_id"] in learners and row["total"]:
                learner = learners[row["learner_id"]]
                entry = path_entry(learner, row["path_id"])
                entry["quiz_attempts"] += 1
                entry["scores"].append({"step_index": row["step_index"], "score": row["score"], "total": row["total"],
                                        "at": row["created_at"]})
                touch(learner, row["created_at"])

        chat_filter = " AND project_id = ?" if project_id else ""
        questions: dict[str, dict[str, Any]] = {}
        for row in self.s.db.query(
            "SELECT learner, content, created_at FROM chat_messages WHERE role = 'user' AND learner IS NOT NULL"
            f"{chat_filter} ORDER BY id", params):
            key = normalize_question(row["content"])
            if not key:
                continue
            entry = questions.setdefault(key, {"question": row["content"], "count": 0, "learners": set(),
                                               "last_asked": row["created_at"]})
            entry["count"] += 1
            entry["learners"].add(row["learner"])
            entry["last_asked"] = row["created_at"]
            for learner in learners.values():
                if learner["name"].lower() == row["learner"].lower():
                    learner["questions_asked"] += 1
                    touch(learner, row["created_at"])

        learner_list = []
        for learner in learners.values():
            path_list = []
            for entry in learner["paths"].values():
                pct = [s["score"] / s["total"] for s in entry["scores"]]
                entry["avg_quiz_score"] = round(sum(pct) / len(pct), 3) if pct else None
                entry["progress"] = round(entry["steps_completed"] / entry["steps_total"], 3) \
                    if entry["steps_total"] else 0.0
                path_list.append(entry)
            all_scores = [s["score"] / s["total"] for e in path_list for s in e["scores"]]
            learner_list.append({
                "id": learner["id"], "name": learner["name"], "last_activity": learner["last_activity"],
                "questions_asked": learner["questions_asked"], "paths": path_list,
                "avg_quiz_score": round(sum(all_scores) / len(all_scores), 3) if all_scores else None,
                "steps_completed": sum(e["steps_completed"] for e in path_list),
                "steps_total": sum(e["steps_total"] for e in path_list),
            })
        learner_list.sort(key=lambda l: l["name"].lower())
        top = sorted(questions.values(), key=lambda q: q["last_asked"], reverse=True)
        top.sort(key=lambda q: -q["count"])  # stable: most asked first, most recent first on ties
        top_questions = [{"question": q["question"], "count": q["count"], "learners": sorted(q["learners"]),
                          "last_asked": q["last_asked"]} for q in top[:15]]
        return {"learners": learner_list, "top_questions": top_questions}
