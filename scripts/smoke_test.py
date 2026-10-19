"""End-to-end smoke test against a running RepoGuide backend (real models, real HTTP API).

    python scripts/smoke_test.py [--base http://127.0.0.1:8000]

Indexes the bundled sample repo, checks the overview, asks questions and validates every citation
against the file on disk (through the API), checks that an unanswerable question is refused,
generates an onboarding path and a quiz, submits a quiz attempt and a feedback record, and reads
the manager dashboard. Prints PASS/FAIL per step; exit code 1 if anything failed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
QUESTIONS = [
    "How is the final order total calculated?",
    "What happens to inventory when an order is cancelled?",
    "How does the frontend send an order to the backend?",
]
UNANSWERABLE = "How does the app charge credit cards through Stripe?"
LEARNER = "Smoke Test"

results: list[tuple[str, bool, str]] = []


def check(step: str, ok: bool, detail: str = "") -> bool:
    results.append((step, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {step}" + (f" - {detail}" if detail else ""), flush=True)
    return ok


def wait_job(client: httpx.Client, job: dict, timeout: float = 900) -> dict:
    deadline = time.time() + timeout
    while job["status"] in ("queued", "running") and time.time() < deadline:
        time.sleep(2)
        job = client.get(f"/api/jobs/{job['id']}").json()
    return job


def ask(client: httpx.Client, project_id: int, question: str) -> tuple[list[dict], dict | None]:
    sources: list[dict] = []
    final = None
    with client.stream("POST", f"/api/projects/{project_id}/chat", json={"question": question, "learner": LEARNER},
                       timeout=600) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            if event["type"] == "sources":
                sources = event["sources"]
            elif event["type"] in ("done", "error"):
                final = event
    return sources, final


def citations_valid(client: httpx.Client, project_id: int, message: dict, sources: list[dict]) -> tuple[bool, str]:
    by_n = {s["n"]: s for s in sources}
    notes = []
    for citation in message["citations"]:
        response = client.get(f"/api/projects/{project_id}/file", params={
            "path": citation["file_path"], "start": citation["start_line"], "end": citation["end_line"]})
        if response.status_code != 200:
            return False, f"{citation['file_path']} could not be read ({response.status_code})"
        data = response.json()
        if not (1 <= citation["start_line"] <= citation["end_line"] <= data["total_lines"]):
            return False, f"{citation['file_path']}:{citation['start_line']}-{citation['end_line']} out of range"
        excerpt = by_n.get(citation["n"], {}).get("content")
        if excerpt is not None and "\n".join(data["lines"]) != excerpt:
            return False, f"[{citation['n']}] lines differ from the excerpt the model saw"
        notes.append(f"[{citation['n']}] {citation['file_path']}:{citation['start_line']}-{citation['end_line']}")
    return bool(message["citations"]), ", ".join(notes) or "no citations"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    client = httpx.Client(base_url=args.base, timeout=60)

    health = client.get("/api/health").json()
    ollama = health["ollama"]
    if not check("Ollama reachable with both models",
                 ollama["reachable"] and ollama["chat_model_available"] and ollama["embed_model_available"],
                 f"{health['config']['chat_model']} + {health['config']['embed_model']}"):
        return 1

    sample = str((ROOT / "eval" / "sample_repo").resolve())
    created = client.post("/api/projects", json={"source": sample, "name": "ShopLite (sample repo)"}).json()
    project_id = created["project"]["id"]
    if created.get("job"):
        job = wait_job(client, created["job"])
    else:  # already registered: run an incremental index
        job = wait_job(client, client.post(f"/api/projects/{project_id}/index", json={}).json())
    project = client.get(f"/api/projects/{project_id}").json()
    check("Index sample repo", job["status"] == "done" and project["status"] == "ready",
          f"{project['file_count']} files, {project['chunk_count']} chunks, "
          f"{(job.get('result') or {}).get('edges', '?')} graph edges")

    overview = client.get(f"/api/projects/{project_id}/overview").json()["overview"] or {}
    described = sum(1 for m in overview.get("modules", []) if m.get("description"))
    check("Overview generated", bool(overview.get("summary")) and overview.get("mermaid", "").startswith("flowchart")
          and described > 0, f"{len(overview.get('tech_stack', []))} stack items, {described} module descriptions, "
          f"{len(overview.get('execution_flow', []))} flow steps")

    first_message = None
    for question in QUESTIONS:
        sources, final = ask(client, project_id, question)
        if final is None or final["type"] != "done":
            check(f"Answer: {question}", False, (final or {}).get("message", "no response"))
            continue
        message = final["message"]
        first_message = first_message or message
        ok, detail = citations_valid(client, project_id, message, sources)
        check(f"Answer with valid citations: {question}", ok and message["grounded"] == 1, detail)

    _, final = ask(client, project_id, UNANSWERABLE)
    refused = final is not None and final["type"] == "done" and final["message"]["grounded"] == 0
    check("Refuses an unanswerable question", refused,
          (final or {}).get("message", {}).get("content", "")[:90] if refused else str(final)[:200])

    job = wait_job(client, client.post(f"/api/projects/{project_id}/onboarding/backend").json())
    path = client.get(f"/api/projects/{project_id}/onboarding/backend").json()["path"]
    check("Onboarding path (backend)", job["status"] == "done" and path and len(path["steps"]) >= 2,
          f"{len(path['steps'])} steps: " + "; ".join(s["title"] for s in path["steps"]) if path else job.get("error", ""))

    if path:
        job = wait_job(client, client.post(f"/api/paths/{path['id']}/steps/0/quiz").json())
        quiz = client.get(f"/api/paths/{path['id']}/steps/0/quiz").json()["quiz"]
        check("Quiz generated for step 1", job["status"] == "done" and quiz and len(quiz["questions"]) >= 2,
              f"{len(quiz['questions'])} questions" if quiz else job.get("error", ""))
        if quiz:
            attempt = client.post(f"/api/quizzes/{quiz['id']}/attempts",
                                  json={"learner": LEARNER, "answers": [0] * len(quiz["questions"])}).json()
            check("Quiz attempt recorded", "score" in attempt,
                  f"score {attempt.get('score')}/{attempt.get('total')}, passed={attempt.get('passed')}")

    if first_message:
        feedback = client.post(f"/api/messages/{first_message['id']}/feedback",
                               json={"rating": -1, "comment": "Smoke test: please mention the shipping rule"}).json()
        listed = client.get(f"/api/projects/{project_id}/feedback").json()
        stored = any(item["id"] == feedback.get("id") for item in listed["items"])
        check("Feedback record stored", stored and feedback.get("rating") == -1,
              f"{len(feedback.get('retrieved_chunk_ids', []))} retrieved chunk ids saved with the rating")

    board = client.get("/api/manager/dashboard", params={"project_id": project_id}).json()
    learner = next((l for l in board["learners"] if l["name"] == LEARNER), None)
    check("Manager dashboard shows the learner", learner is not None,
          f"questions asked {learner['questions_asked']}, avg quiz {learner['avg_quiz_score']}" if learner else "")

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
