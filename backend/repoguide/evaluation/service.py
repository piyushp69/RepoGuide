"""Run an evaluation set against a project and save JSON + Markdown reports.

Metrics
- hit@5 / MRR@10: is a chunk that contains the expected symbol (in the expected file)
  among the top retrieved chunks, and at what rank? Reported at symbol level and at
  file level.
- in-context rate: is a relevant chunk anywhere in what the model actually sees
  (top-k plus graph neighbours)?
- citation validity: do the files and line ranges cited by generated answers exist on
  disk and still match the indexed code? Inline path mentions in answer text and
  out-of-range [n] markers are counted too.
- abstention: for questions the code cannot answer, does the model say so?
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..ingest.walker import read_text
from ..parsing.chunker import split_lines
from ..qa.citations import path_mentions
from ..util import truncate, utcnow
from . import metrics as m
from ..errors import InvalidInputError, NotFoundError

if TYPE_CHECKING:
    from ..services import Services

log = logging.getLogger(__name__)
TOP_K = 5
MRR_CUTOFF = 10


class _NullReporter:
    def update(self, *args: Any, **kwargs: Any) -> None:
        pass

    def flush(self) -> None:
        pass


class EvaluationService:
    def __init__(self, services: "Services"):
        self.s = services

    # ----- inputs ---------------------------------------------------------------------------------
    def load_sample_set(self, path: Path | None = None) -> dict[str, Any]:
        path = path or self.s.settings.eval_set_path
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        data["path"] = str(path)
        return data

    def custom_set(self, project_id: int) -> dict[str, Any]:
        rows = self.s.feedback.eval_questions(project_id)
        name = self.s.db.scalar("SELECT name FROM projects WHERE id = ?", (project_id,)) or str(project_id)
        return {
            "name": f"feedback-{project_id}",
            "description": f"Questions added from thumbs-down feedback on {name}",
            "questions": [{"id": f"f{r['id']}", "question": r["question"], "expected_file": r["expected_file"],
                           "expected_symbol": r["expected_symbol"]} for r in rows],
            "unanswerable": [],
        }

    def sample_project(self, reporter: Any = None) -> int:
        """Register (if needed) and incrementally index the bundled sample repository."""
        root = self.s.settings.sample_repo_dir.resolve()
        row = self.s.db.query_one("SELECT id FROM projects WHERE root_path = ?", (str(root),))
        project_id = row["id"] if row else self.s.projects.create(str(root), name="ShopLite (sample repo)")["id"]
        self.s.indexer.index_project(project_id, reporter or _NullReporter())
        return project_id

    # ----- run -----------------------------------------------------------------------------------
    def run(self, eval_set: dict[str, Any], project_id: int, with_llm: bool = True, limit: int | None = None,
            reporter: Any = None) -> dict[str, Any]:
        reporter = reporter or _NullReporter()
        started = time.perf_counter()
        project = self.s.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if project is None:
            raise NotFoundError("Project not found")
        questions = eval_set.get("questions", [])[: limit or None]
        unanswerable = eval_set.get("unanswerable", []) if with_llm else []
        if limit:
            unanswerable = unanswerable[: max(1, limit // 5)]
        if not questions:
            raise InvalidInputError("The evaluation set has no questions.")

        root = Path(project["root_path"])
        known_paths = [r["path"] for r in self.s.db.query("SELECT path FROM files WHERE project_id = ?", (project_id,))]
        file_lines: dict[str, list[str]] = {}
        for path in known_paths:
            try:
                file_lines[path] = split_lines(read_text(root / path)[1])
            except OSError:
                continue

        results = []
        total = len(questions) + len(unanswerable)
        for index, item in enumerate(questions, start=1):
            reporter.update(phase="evaluating", current=index - 1, total=total, message=truncate(item["question"], 80))
            results.append(self._evaluate_question(project_id, item, with_llm, file_lines, known_paths))
        abstentions = []
        for offset, item in enumerate(unanswerable, start=len(questions) + 1):
            reporter.update(phase="evaluating", current=offset - 1, total=total, message=truncate(item["question"], 80))
            t0 = time.perf_counter()
            outcome = self.s.qa.run_once(project_id, item["question"])
            abstentions.append({"id": item.get("id"), "question": item["question"], "answer": outcome["answer"],
                                "abstained": outcome["not_found"], "seconds": round(time.perf_counter() - t0, 2)})
        reporter.update(phase="evaluating", current=total, total=total, message="Computing metrics")

        report = self._report(eval_set, project, results, abstentions, with_llm, time.perf_counter() - started)
        self.save(report)
        return report

    def _symbol_ranges(self, project_id: int, expected_file: str, expected_symbol: str | None) -> tuple[list, bool]:
        if not expected_symbol:
            return [], True
        rows = self.s.db.query(
            "SELECT start_line, end_line FROM symbols WHERE project_id = ? AND file_path = ? "
            "AND (qualified_name = ? OR name = ?)", (project_id, expected_file, expected_symbol, expected_symbol))
        return [(r["start_line"], r["end_line"]) for r in rows], bool(rows)

    def _evaluate_question(self, project_id: int, item: dict[str, Any], with_llm: bool,
                           file_lines: dict[str, list[str]], known_paths: list[str]) -> dict[str, Any]:
        question, expected_file = item["question"], item["expected_file"]
        expected_symbol = item.get("expected_symbol")
        ranges, symbol_found = self._symbol_ranges(project_id, expected_file, expected_symbol)

        def ranked(mode: str) -> list[dict[str, Any]]:
            result = self.s.retriever.retrieve(project_id, question, top_k=MRR_CUTOFF, mode=mode, expand=False)
            return [c.summary() for c in result.chunks]

        hybrid = ranked("hybrid")
        rank = m.first_relevant_rank(hybrid, expected_file, ranges)
        file_rank = m.first_relevant_rank(hybrid, expected_file, [])
        ablation = {}
        for mode in ("vector", "bm25"):
            mode_rank = m.first_relevant_rank(ranked(mode), expected_file, ranges)
            ablation[mode] = {"rank": mode_rank}
        ablation["hybrid"] = {"rank": rank}
        context = self.s.retriever.retrieve(project_id, question)
        in_context = any(m.is_relevant(c.summary(), expected_file, ranges) for c in context.chunks)

        record: dict[str, Any] = {
            "id": item.get("id"), "question": question, "expected_file": expected_file,
            "expected_symbol": expected_symbol, "symbol_found_in_index": symbol_found,
            "rank": rank, "file_rank": file_rank, "hit_at_5": m.hit_at_k(rank, TOP_K),
            "file_hit_at_5": m.hit_at_k(file_rank, TOP_K), "reciprocal_rank": m.reciprocal_rank(rank, MRR_CUTOFF),
            "file_reciprocal_rank": m.reciprocal_rank(file_rank, MRR_CUTOFF), "in_context": in_context,
            "ablation": ablation,
            "top5": [{"file_path": c["file_path"], "symbol": c["symbol"], "lines": f"{c['start_line']}-{c['end_line']}"}
                     for c in hybrid[:TOP_K]],
        }
        if with_llm:
            t0 = time.perf_counter()
            outcome = self.s.qa.run_once(project_id, question)
            record["seconds"] = round(time.perf_counter() - t0, 2)
            record["answer"] = outcome["answer"]
            record["not_found"] = outcome["not_found"]
            record["rewritten"] = bool(outcome.get("rewritten"))
            content_by_chunk = {b.chunk.id: b.content for b in outcome["blocks"]}
            checks = []
            for citation in outcome["citations"]:
                valid, reason = m.check_citation(citation, file_lines, content_by_chunk.get(citation["chunk_id"]))
                checks.append({"kind": "marker", "n": citation["n"], "file_path": citation["file_path"],
                               "start_line": citation["start_line"], "end_line": citation["end_line"],
                               "valid": valid, "reason": reason})
            for number in outcome["invalid_markers"]:
                checks.append({"kind": "marker", "n": number, "file_path": None, "start_line": None, "end_line": None,
                               "valid": False, "reason": "cited excerpt number does not exist"})
            for mention in path_mentions(outcome["answer"]):
                resolved, valid, reason = m.check_mention(mention, known_paths, file_lines)
                checks.append({"kind": "mention", "file_path": resolved, "start_line": mention["start_line"],
                               "end_line": mention["end_line"], "valid": valid, "reason": reason})
            record["citations"] = checks
            record["has_citation"] = bool(outcome["citations"])
            record["cites_expected_file"] = any(c["file_path"] == expected_file for c in outcome["citations"])
            record["cites_expected_symbol"] = any(m.is_relevant(c, expected_file, ranges) for c in outcome["citations"])
            record["citation_corrections"] = len(outcome.get("citation_corrections") or [])
        return record

    # ----- reporting ------------------------------------------------------------------------------
    def _report(self, eval_set: dict[str, Any], project: dict[str, Any], results: list[dict[str, Any]],
                abstentions: list[dict[str, Any]], with_llm: bool, seconds: float) -> dict[str, Any]:
        def rate(key: str) -> float | None:
            return m.mean(1.0 if r[key] else 0.0 for r in results)

        metrics: dict[str, Any] = {
            "questions": len(results),
            "hit_at_5": rate("hit_at_5"),
            "mrr": m.mean(r["reciprocal_rank"] for r in results),
            "file_hit_at_5": rate("file_hit_at_5"),
            "file_mrr": m.mean(r["file_reciprocal_rank"] for r in results),
            "in_context_rate": rate("in_context"),
        }
        ablation = {}
        for mode in ("vector", "bm25", "hybrid"):
            ranks = [r["ablation"][mode]["rank"] for r in results]
            ablation[mode] = {"hit_at_5": m.mean(1.0 if m.hit_at_k(x, TOP_K) else 0.0 for x in ranks),
                              "mrr": m.mean(m.reciprocal_rank(x, MRR_CUTOFF) for x in ranks)}
        if with_llm:
            checks = [c for r in results for c in r.get("citations", [])]
            metrics.update({
                "citation_validity": m.mean(1.0 if c["valid"] else 0.0 for c in checks),
                "citations_checked": len(checks),
                "answers_with_citations": rate("has_citation"),
                "answers_citing_expected_file": rate("cites_expected_file"),
                "answers_citing_expected_symbol": rate("cites_expected_symbol"),
                "answers_rewritten": rate("rewritten"),
                "answers_with_corrected_citations": m.mean(1.0 if r["citation_corrections"] else 0.0 for r in results),
                "avg_answer_seconds": m.mean(r["seconds"] for r in results),
            })
            if abstentions:
                metrics["abstention_rate"] = m.mean(1.0 if a["abstained"] else 0.0 for a in abstentions)
                metrics["unanswerable_questions"] = len(abstentions)
        now = datetime.now(timezone.utc)
        set_name = re.sub(r"[^A-Za-z0-9_-]+", "_", str(eval_set.get("name") or "eval"))
        return {
            "run_id": now.strftime("%Y%m%d-%H%M%S") + f"-{set_name}",
            "created_at": utcnow(),
            "eval_set": {"name": eval_set.get("name"), "description": eval_set.get("description"),
                         "path": eval_set.get("path"), "questions": len(results),
                         "unanswerable": len(abstentions)},
            "project": {"id": project["id"], "name": project["name"], "root_path": project["root_path"]},
            "config": {"chat_model": self.s.settings.chat_model, "embed_model": self.s.settings.embed_model,
                       "top_k": TOP_K, "mrr_cutoff": MRR_CUTOFF, "with_llm": with_llm,
                       "retrieval_top_k": self.s.settings.retrieval_top_k},
            "metrics": metrics,
            "ablation": ablation,
            "questions": results,
            "unanswerable": abstentions,
            "duration_seconds": round(seconds, 1),
        }

    def save(self, report: dict[str, Any]) -> tuple[Path, Path]:
        directory = self.s.settings.eval_results_dir
        directory.mkdir(parents=True, exist_ok=True)
        json_text = json.dumps(report, indent=2, ensure_ascii=False)
        markdown = render_markdown(report)
        json_path = directory / f"{report['run_id']}.json"
        md_path = directory / f"{report['run_id']}.md"
        json_path.write_text(json_text, encoding="utf-8")
        md_path.write_text(markdown, encoding="utf-8")
        (directory / "latest.json").write_text(json_text, encoding="utf-8")
        (directory / "latest.md").write_text(markdown, encoding="utf-8")
        report["files"] = {"json": str(json_path), "markdown": str(md_path)}
        return json_path, md_path

    def history(self) -> list[dict[str, Any]]:
        directory = self.s.settings.eval_results_dir
        runs = []
        if not directory.is_dir():
            return runs
        for path in sorted(directory.glob("*.json"), reverse=True):
            if path.name == "latest.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs.append({"run_id": data.get("run_id"), "created_at": data.get("created_at"),
                         "eval_set": data.get("eval_set"), "project": data.get("project"),
                         "config": data.get("config"), "metrics": data.get("metrics"),
                         "ablation": data.get("ablation"), "duration_seconds": data.get("duration_seconds")})
        return runs

    def get(self, run_id: str) -> dict[str, Any] | None:
        directory = self.s.settings.eval_results_dir.resolve()
        path = (directory / f"{run_id}.json").resolve()
        if path.parent != directory or not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    metrics, config = report["metrics"], report["config"]
    lines = [
        f"# RepoGuide evaluation — {report['created_at']}",
        "",
        f"- Evaluation set: **{report['eval_set']['name']}** ({report['eval_set']['questions']} questions"
        + (f", {report['eval_set']['unanswerable']} unanswerable" if report["eval_set"]["unanswerable"] else "") + ")",
        f"- Project: {report['project']['name']} (`{report['project']['root_path']}`)",
        f"- Models: chat `{config['chat_model']}`, embeddings `{config['embed_model']}`",
        f"- Answer generation: {'on' if config['with_llm'] else 'off (retrieval only)'}",
        f"- Duration: {report['duration_seconds']} s",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Hit rate @{config['top_k']} (expected symbol) | {_fmt(metrics['hit_at_5'])} |",
        f"| MRR @{config['mrr_cutoff']} (expected symbol) | {_fmt(metrics['mrr'])} |",
        f"| Hit rate @{config['top_k']} (expected file) | {_fmt(metrics['file_hit_at_5'])} |",
        f"| MRR @{config['mrr_cutoff']} (expected file) | {_fmt(metrics['file_mrr'])} |",
        f"| Relevant chunk in answer context (top-{config['retrieval_top_k']} + graph) | {_fmt(metrics['in_context_rate'])} |",
    ]
    if config["with_llm"]:
        lines += [
            f"| Citation validity ({metrics.get('citations_checked', 0)} citations checked) | {_fmt(metrics.get('citation_validity'))} |",
            f"| Answers with at least one citation | {_fmt(metrics.get('answers_with_citations'))} |",
            f"| Answers citing the expected file | {_fmt(metrics.get('answers_citing_expected_file'))} |",
            f"| Answers citing the expected symbol | {_fmt(metrics.get('answers_citing_expected_symbol'))} |",
            f"| Drafts rewritten because they cited nothing | {_fmt(metrics.get('answers_rewritten'))} |",
            f"| Answers with a citation number corrected | {_fmt(metrics.get('answers_with_corrected_citations'))} |",
            f"| Average answer time (s) | {_fmt(metrics.get('avg_answer_seconds'))} |",
        ]
        if "abstention_rate" in metrics:
            lines.append(f"| Abstention on unanswerable questions | {_fmt(metrics['abstention_rate'])} |")
    lines += ["", "## Retrieval ablation", "", "| Retriever | Hit@5 | MRR@10 |", "|---|---|---|"]
    for mode, values in report["ablation"].items():
        lines.append(f"| {mode} | {_fmt(values['hit_at_5'])} | {_fmt(values['mrr'])} |")
    lines += ["", "## Per-question results", "",
              "| ID | Question | Expected | Rank | Hit@5 | In context | Citations valid | Cites expected file |",
              "|---|---|---|---|---|---|---|---|"]
    for r in report["questions"]:
        expected = f"`{r['expected_file']}`" + (f" · `{r['expected_symbol']}`" if r["expected_symbol"] else "")
        citations = r.get("citations")
        valid = f"{sum(c['valid'] for c in citations)}/{len(citations)}" if citations is not None else "–"
        cites = ("yes" if r.get("cites_expected_file") else "no") if "cites_expected_file" in r else "–"
        lines.append(f"| {r['id']} | {r['question'].replace('|', '/')} | {expected} | {_fmt(r['rank'])} | "
                     f"{'yes' if r['hit_at_5'] else 'no'} | {'yes' if r['in_context'] else 'no'} | {valid} | {cites} |")
    if report["unanswerable"]:
        lines += ["", "## Unanswerable questions (the model should say it cannot find the answer)", "",
                  "| ID | Question | Abstained | Answer (start) |", "|---|---|---|---|"]
        for a in report["unanswerable"]:
            answer = truncate(" ".join(a["answer"].split()), 140).replace("|", "/")
            lines.append(f"| {a['id']} | {a['question']} | {'yes' if a['abstained'] else 'no'} | {answer} |")
    misses = [r for r in report["questions"] if not r["hit_at_5"]]
    if misses:
        lines += ["", "## Misses (expected symbol not in top 5)", ""]
        for r in misses:
            got = ", ".join(f"`{t['file_path']}:{t['lines']}` ({t['symbol']})" for t in r["top5"])
            lines.append(f"- **{r['id']}** {r['question']} — expected `{r['expected_file']}` "
                         f"{('· `' + r['expected_symbol'] + '`') if r['expected_symbol'] else ''}; got {got}")
    bad = [(r, c) for r in report["questions"] for c in r.get("citations", []) if not c["valid"]]
    if bad:
        lines += ["", "## Invalid citations", ""]
        for r, c in bad:
            lines.append(f"- **{r['id']}** {c['kind']} `{c['file_path']}:{c['start_line']}-{c['end_line']}` — {c['reason']}")
    return "\n".join(lines) + "\n"
