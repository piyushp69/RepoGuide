"""End-to-end API flow with the fake models: projects, indexing, overview, chat, feedback, onboarding."""

from __future__ import annotations

import json
import time

import pytest


def wait_job(client, job: dict, timeout: float = 60.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = client.get(f"/api/jobs/{job['id']}").json()
        if current["status"] in ("done", "error"):
            return current
        time.sleep(0.05)
    raise AssertionError(f"job {job['id']} did not finish")


@pytest.fixture
def indexed(client, services):
    response = client.post("/api/projects", json={"source": str(services.settings.sample_repo_dir)})
    assert response.status_code == 201
    body = response.json()
    job = wait_job(client, body["job"])
    assert job["status"] == "done", job
    return body["project"]["id"]


def ask(client, project_id: int, question: str, learner: str | None = None) -> list[dict]:
    response = client.post(f"/api/projects/{project_id}/chat", json={"question": question, "learner": learner})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["config"]["chat_model"] and body["config"]["embed_model"]
    assert "reachable" in body["ollama"]


def test_project_lifecycle_files_and_overview(client, indexed):
    projects = client.get("/api/projects").json()
    project = next(p for p in projects if p["id"] == indexed)
    assert project["status"] == "ready" and project["file_count"] == 15 and project["chunk_count"] > 50
    assert project["job"]["status"] == "done" and project["job"]["result"]["added"] == 15

    files = client.get(f"/api/projects/{indexed}/files").json()
    assert {"backend/app.py", "frontend/src/cart.ts"} <= {f["path"] for f in files}
    snippet = client.get(f"/api/projects/{indexed}/file",
                         params={"path": "backend/services/pricing.py", "start": 38, "end": 43, "context": 2}).json()
    assert snippet["view_start"] == 36 and snippet["lines"][2].startswith("def compute_order_total")
    # Only indexed files can be read: no path traversal.
    assert client.get(f"/api/projects/{indexed}/file", params={"path": "../../../windows/win.ini"}).status_code == 404
    symbols = client.get(f"/api/projects/{indexed}/symbols", params={"path": "frontend/src/cart.ts"}).json()
    assert "Cart.subtotalCents" in {s["qualified_name"] for s in symbols}

    overview = client.get(f"/api/projects/{indexed}/overview").json()["overview"]
    assert overview["summary"]
    assert overview["mermaid"].startswith("flowchart LR")
    assert {"Python", "TypeScript", "Flask", "React"} <= {t["name"] for t in overview["tech_stack"]}
    assert overview["entry_points"][0]["file"] == "backend/app.py"
    assert any(m["path"] == "backend/services" and m["description"] for m in overview["modules"])
    assert overview["execution_flow"] and not overview["stale"]

    # Re-adding the same folder returns the existing project.
    again = client.post("/api/projects", json={"source": project["root_path"], "index": False}).json()
    assert again["project"]["id"] == indexed

    search = client.post(f"/api/projects/{indexed}/search", json={"query": "reserve stock", "top_k": 5}).json()
    assert search["results"] and search["results"][0]["content"]


def test_bad_inputs(client, indexed, tmp_path):
    assert client.post("/api/projects", json={"source": str(tmp_path / "nope")}).status_code == 400
    assert client.post("/api/projects", json={"source": "https://gitlab.com/a/b"}).status_code == 400
    assert client.get("/api/projects/9999").status_code == 404
    assert client.get(f"/api/projects/{indexed}/onboarding/astronaut").status_code == 404
    empty = client.post("/api/projects", json={"source": str(tmp_path), "index": False}).json()["project"]
    assert client.post(f"/api/projects/{empty['id']}/chat", json={"question": "hi"}).status_code == 409


def test_chat_stream_history_and_feedback(client, indexed):
    events = ask(client, indexed, "How is the final order total calculated?", learner="Asha")
    kinds = [e["type"] for e in events]
    assert kinds[0] == "sources" and kinds[-1] == "done" and "token" in kinds
    sources = events[0]["sources"]
    assert sources[0]["n"] == 1 and sources[0]["file_path"] and sources[0]["content"]
    message = events[-1]["message"]
    assert message["citations"] and message["citations"][0]["file_path"] == sources[0]["file_path"]
    assert message["citations"][0]["start_line"] == sources[0]["start_line"]
    assert message["grounded"] == 1

    history = client.get(f"/api/projects/{indexed}/chat", params={"learner": "Asha"}).json()
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert client.get(f"/api/projects/{indexed}/chat").json() == []  # other learners have their own history

    # Feedback: thumbs down with a comment, then curate it into the evaluation set.
    fb = client.post(f"/api/messages/{message['id']}/feedback", json={"rating": -1, "comment": "Missed tax"}).json()
    assert fb["rating"] == -1 and fb["retrieved_chunk_ids"] and fb["question"].startswith("How is")
    fb = client.post(f"/api/messages/{message['id']}/feedback", json={"rating": 1}).json()  # changing one's mind
    assert fb["rating"] == 1
    fb = client.post(f"/api/messages/{message['id']}/feedback", json={"rating": -1, "comment": "Missed tax"}).json()
    listing = client.get(f"/api/projects/{indexed}/feedback").json()
    assert len(listing["items"]) == 1 and listing["stats"]["down"] == 1 and listing["stats"]["by_day"]
    assert client.post(f"/api/messages/{message['id']}/feedback", json={"rating": 5}).status_code == 400

    bad = client.post(f"/api/feedback/{fb['id']}/eval", json={"expected_file": "nope.py"})
    assert bad.status_code == 400
    added = client.post(f"/api/feedback/{fb['id']}/eval",
                        json={"expected_file": "backend/services/pricing.py", "expected_symbol": "calculate_tax"})
    assert added.status_code == 200 and added.json()["expected_symbol"] == "calculate_tax"
    assert len(client.get(f"/api/projects/{indexed}/eval-questions").json()) == 1

    export = client.get(f"/api/projects/{indexed}/feedback/export")
    records = [json.loads(line) for line in export.text.splitlines()]
    assert len(records) == 1 and records[0]["label_source"] == "thumbs_down_corrected"
    assert records[0]["positive"]["symbol"] == "calculate_tax" and records[0]["positive"]["text"]
    assert all(n["file_path"] != "backend/services/pricing.py" for n in records[0]["negatives"])

    assert client.delete(f"/api/projects/{indexed}/chat", params={"learner": "Asha"}).json()["deleted"] == 2


def test_onboarding_quiz_progress_and_dashboard(client, indexed):
    assert client.post("/api/learners", json={"name": "Ravi"}).status_code == 201
    assert {r["id"] for r in client.get("/api/roles").json()} == {"frontend", "backend", "data_ml", "fullstack"}
    assert client.get(f"/api/projects/{indexed}/onboarding/backend").json()["path"] is None

    job = wait_job(client, client.post(f"/api/projects/{indexed}/onboarding/backend").json())
    assert job["status"] == "done", job
    path = client.get(f"/api/projects/{indexed}/onboarding/backend").json()["path"]
    assert len(path["steps"]) >= 2 and all(step["files"] for step in path["steps"])

    quiz_job = wait_job(client, client.post(f"/api/paths/{path['id']}/steps/1/quiz").json())
    assert quiz_job["status"] == "done", quiz_job
    quiz = client.get(f"/api/paths/{path['id']}/steps/1/quiz").json()["quiz"]
    assert len(quiz["questions"]) >= 2
    assert all("answer_index" not in q and len(q["options"]) == 4 for q in quiz["questions"])  # answers hidden

    stored = client.app.state.services.onboarding.quiz(quiz["id"], include_answers=True)
    correct = [q["answer_index"] for q in stored["questions"]]
    result = client.post(f"/api/quizzes/{quiz['id']}/attempts", json={"learner": "Ravi", "answers": correct}).json()
    assert result["score"] == result["total"] and result["passed"]
    assert all(r["explanation"] and r["line"] for r in result["results"])

    progress = client.post(f"/api/paths/{path['id']}/progress",
                           json={"learner": "Ravi", "step_index": 0, "completed": True}).json()
    assert progress["completed_steps"] == [0, 1]  # step 1 auto-completed by passing its quiz
    assert progress["quiz_scores"]["1"]["best"] == 1.0

    ask(client, indexed, "Where are passwords hashed?", learner="Ravi")
    ask(client, indexed, "where are passwords hashed", learner="Ravi")
    board = client.get("/api/manager/dashboard", params={"project_id": indexed}).json()
    ravi = next(l for l in board["learners"] if l["name"] == "Ravi")
    assert ravi["steps_completed"] == 2 and ravi["avg_quiz_score"] == 1.0 and ravi["questions_asked"] == 2
    assert board["top_questions"][0]["count"] == 2 and board["top_questions"][0]["learners"] == ["Ravi"]


def test_evaluation_endpoints(client, services):
    job = client.post("/api/eval/run", json={"source": "sample", "with_llm": True, "limit": 5}).json()
    job = wait_job(client, job, timeout=120)
    assert job["status"] == "done", job
    runs = client.get("/api/eval/runs").json()
    assert runs and runs[0]["run_id"] == job["result"]["run_id"]
    latest = client.get("/api/eval/latest").json()
    assert latest["metrics"]["questions"] == 5 and "citation_validity" in latest["metrics"]
    assert client.get(f"/api/eval/runs/{latest['run_id']}").status_code == 200
    assert client.get("/api/eval/runs/..%2F..%2Fsecret").status_code == 404
    assert len(client.get("/api/eval/set").json()["questions"]) >= 20
