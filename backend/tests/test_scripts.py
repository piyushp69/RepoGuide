"""The command-line evaluation and feedback-export scripts."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


def test_evaluate_script_runs_and_saves_reports(services, monkeypatch, capsys):
    script = load_script("evaluate")
    monkeypatch.setattr("repoguide.services.build_services", lambda **kwargs: services)
    monkeypatch.setattr(services.ollama, "health", lambda *args: {
        "reachable": True, "error": None, "models": [], "chat_model_available": True, "embed_model_available": True})
    monkeypatch.setattr(services, "close", lambda: None)  # the fixture owns the services
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--in-process", "--limit", "4", "--json"])
    assert script.main() == 0
    out = capsys.readouterr().out
    assert "Hit rate @5 (expected symbol)" in out and "Citation validity" in out and "Retrieval ablation" in out
    latest = json.loads((services.settings.eval_results_dir / "latest.json").read_text(encoding="utf-8"))
    assert latest["metrics"]["questions"] == 4 and latest["config"]["with_llm"] is True


def test_evaluate_script_reports_missing_ollama(services, monkeypatch, capsys):
    script = load_script("evaluate")
    monkeypatch.setattr("repoguide.services.build_services", lambda **kwargs: services)
    monkeypatch.setattr(services.ollama, "health", lambda *args: {
        "reachable": False, "error": "connection refused", "models": [],
        "chat_model_available": False, "embed_model_available": False})
    monkeypatch.setattr(services, "close", lambda: None)
    monkeypatch.setattr(sys, "argv", ["evaluate.py", "--in-process"])
    assert script.main() == 2
    assert "Ollama is not reachable" in capsys.readouterr().err


def test_export_feedback_script(services, sample_project, monkeypatch, tmp_path, capsys):
    answer = services.qa.answer(sample_project, "How are passwords hashed?")
    assert answer["citations"]
    services.feedback.submit(answer["id"], 1)
    script = load_script("export_feedback")
    monkeypatch.setattr(script, "get_settings", lambda: services.settings)
    out_file = tmp_path / "pairs.jsonl"
    monkeypatch.setattr(sys, "argv", ["export_feedback.py", "--out", str(out_file)])
    assert script.main() == 0
    records = [json.loads(line) for line in out_file.read_text(encoding="utf-8").splitlines()]
    assert len(records) == len(answer["citations"])
    record = records[0]
    assert record["label_source"] == "thumbs_up_cited" and record["query"] == "How are passwords hashed?"
    assert record["positive"]["chunk_id"] == answer["citations"][0]["chunk_id"] and record["positive"]["text"]
    assert record["positive"]["chunk_id"] not in {n["chunk_id"] for n in record["negatives"]}
    assert "Wrote 1 training pairs" in capsys.readouterr().out
