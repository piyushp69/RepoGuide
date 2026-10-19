"""Evaluation metrics, the evaluation set itself, and a full harness run."""

from __future__ import annotations

import json

from repoguide.evaluation import metrics as m
from repoguide.ingest.walker import read_text
from repoguide.parsing import parse_file

from .conftest import EVAL_SET, SAMPLE_REPO


def chunk(path: str, start: int, end: int) -> dict:
    return {"file_path": path, "start_line": start, "end_line": end}


def test_relevance_rank_hit_and_mrr():
    ranked = [chunk("a.py", 1, 10), chunk("b.py", 1, 20), chunk("b.py", 30, 60), chunk("c.py", 1, 5)]
    ranges = [(40, 50)]
    assert not m.is_relevant(ranked[1], "b.py", ranges)  # right file, wrong symbol
    assert m.is_relevant(ranked[2], "b.py", ranges)
    assert m.first_relevant_rank(ranked, "b.py", ranges) == 3
    assert m.first_relevant_rank(ranked, "b.py", []) == 2  # file-level
    assert m.first_relevant_rank(ranked, "z.py", []) is None
    assert m.hit_at_k(3, 5) and not m.hit_at_k(6, 5) and not m.hit_at_k(None, 5)
    assert m.reciprocal_rank(3) == 1 / 3 and m.reciprocal_rank(None) == 0.0 and m.reciprocal_rank(11, 10) == 0.0
    assert m.mean([1.0, 0.0, 0.5]) == 0.5 and m.mean([]) is None


def test_citation_checks():
    files = {"src/a.py": ["def f():", "    return 1", "", "x = f()"]}
    assert m.check_citation({"file_path": "src/a.py", "start_line": 1, "end_line": 2}, files) == (True, "ok")
    assert not m.check_citation({"file_path": "src/b.py", "start_line": 1, "end_line": 2}, files)[0]
    assert not m.check_citation({"file_path": "src/a.py", "start_line": 3, "end_line": 9}, files)[0]
    assert not m.check_citation({"file_path": "src/a.py", "start_line": 0, "end_line": 1}, files)[0]
    valid, _ = m.check_citation({"file_path": "src/a.py", "start_line": 1, "end_line": 2}, files,
                                "def f():\n    return 1")
    assert valid
    stale, reason = m.check_citation({"file_path": "src/a.py", "start_line": 1, "end_line": 2}, files,
                                     "def g():\n    return 2")
    assert not stale and "changed" in reason
    assert m.mention_matches("a.py", files) == ["src/a.py"] and m.mention_matches("q.py", files) == []


def test_mentions_of_a_shared_file_name_are_not_called_missing():
    files = {"app/main.py": ["import x"] * 40, "app/api/main.py": ["y = 1"] * 5, "app/util.py": ["z = 2"]}
    path, valid, reason = m.check_mention({"path": "main.py", "start_line": None, "end_line": None}, files, files)
    assert valid and path == "main.py" and "shared by 2 files" in reason
    path, valid, _ = m.check_mention({"path": "main.py", "start_line": 30, "end_line": 32}, files, files)
    assert valid and path == "app/main.py"  # only the longer main.py has line 32
    assert not m.check_mention({"path": "main.py", "start_line": 90, "end_line": 91}, files, files)[1]
    path, valid, reason = m.check_mention({"path": "payments.py", "start_line": None, "end_line": None}, files, files)
    assert not valid and reason == "file does not exist"
    assert m.check_mention({"path": "util.py", "start_line": 1, "end_line": 1}, files, files)[:2] == ("app/util.py", True)


def test_eval_set_is_well_formed():
    """Every question names a file and symbol that really exist in the sample repo."""
    data = json.loads(EVAL_SET.read_text(encoding="utf-8"))
    questions = data["questions"]
    assert len(questions) >= 20 and len({q["id"] for q in questions}) == len(questions)
    assert len(data["unanswerable"]) >= 1
    for q in questions:
        path = SAMPLE_REPO / q["expected_file"]
        assert path.is_file(), q
        _, text = read_text(path)
        parsed = parse_file(q["expected_file"], text, "python" if path.suffix == ".py" else "typescript")
        names = {s.qualified_name for s in parsed.symbols} | {s.name for s in parsed.symbols}
        assert q["expected_symbol"] in names, q


def test_full_evaluation_run_writes_reports(services):
    project_id = services.evaluation.sample_project()
    eval_set = services.evaluation.load_sample_set()
    report = services.evaluation.run(eval_set, project_id, with_llm=True, limit=6)
    metrics = report["metrics"]
    assert metrics["questions"] == 6
    for key in ("hit_at_5", "mrr", "file_hit_at_5", "file_mrr", "in_context_rate", "citation_validity",
                "answers_with_citations", "abstention_rate"):
        assert metrics[key] is not None and 0.0 <= metrics[key] <= 1.0, key
    assert set(report["ablation"]) == {"vector", "bm25", "hybrid"}
    # The fake model cites excerpt [1] with its real path, so every citation must validate.
    assert metrics["citation_validity"] == 1.0 and metrics["citations_checked"] >= 6
    results_dir = services.settings.eval_results_dir
    assert (results_dir / f"{report['run_id']}.json").is_file() and (results_dir / "latest.md").is_file()
    markdown = (results_dir / f"{report['run_id']}.md").read_text(encoding="utf-8")
    assert "Hit rate @5" in markdown and "Citation validity" in markdown
    history = services.evaluation.history()
    assert history[0]["run_id"] == report["run_id"]

    # A retrieval-only run skips answer generation and its metrics.
    quick = services.evaluation.run(eval_set, project_id, with_llm=False, limit=3)
    assert "citation_validity" not in quick["metrics"] and quick["unanswerable"] == []


def test_citation_validity_detects_bad_citations(services, monkeypatch):
    project_id = services.evaluation.sample_project()

    def bad_answer(messages, temperature=None, num_predict=None):
        return "See [1] and [42], plus `backend/missing.py` and backend/app.py:500-510."

    monkeypatch.setattr(services.llm, "complete", bad_answer)
    report = services.evaluation.run(services.evaluation.load_sample_set(), project_id, with_llm=True, limit=1)
    checks = report["questions"][0]["citations"]
    reasons = sorted(c["reason"] for c in checks if not c["valid"])
    assert any("does not exist" in r for r in reasons) and any("past the end" in r for r in reasons)
    assert report["metrics"]["citation_validity"] < 1.0
