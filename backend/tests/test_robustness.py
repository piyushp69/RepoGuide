"""Edge cases found in the audit: empty projects, the model going away, and restarts mid-index."""

from __future__ import annotations

from repoguide.jobs import JobManager
from repoguide.llm.ollama import OllamaError
from repoguide.overview.mermaid import build_mermaid

from .test_api import ask, wait_job


def test_empty_project_overview_is_factual_and_skips_the_model(client, services, tmp_path):
    folder = tmp_path / "empty_project"
    folder.mkdir()
    body = client.post("/api/projects", json={"source": str(folder)}).json()
    assert wait_job(client, body["job"])["status"] == "done"
    project_id = body["project"]["id"]

    overview = client.get(f"/api/projects/{project_id}/overview").json()["overview"]
    assert overview["summary"].startswith("No supported source files were found")
    assert overview["mermaid"] == ""
    assert overview["modules"] == [] and overview["execution_flow"] == []
    assert services.llm.calls == []  # the model was never asked to describe a project it cannot see

    response = client.post(f"/api/projects/{project_id}/chat", json={"question": "What does this do?"})
    assert response.status_code == 409
    assert "no supported source files" in response.json()["detail"]


def test_not_indexed_and_still_indexing_have_distinct_messages(client, services, tmp_path):
    project = client.post("/api/projects", json={"source": str(tmp_path), "index": False}).json()["project"]
    response = client.post(f"/api/projects/{project['id']}/chat", json={"question": "hi"})
    assert response.status_code == 409 and response.json()["detail"] == "This project has not been indexed yet."

    services.db.execute("UPDATE projects SET status = 'indexing' WHERE id = ?", (project["id"],))
    response = client.post(f"/api/projects/{project['id']}/search", json={"query": "hi"})
    assert response.status_code == 409 and "still being indexed" in response.json()["detail"]


def test_overview_keeps_previous_text_when_the_model_is_unavailable(services, sample_project, monkeypatch):
    services.overview.generate(sample_project)
    before = services.overview.get(sample_project)
    assert before["summary"] and before["execution_flow"] and not before["warnings"]

    def unavailable(*args, **kwargs):
        raise OllamaError("Cannot reach Ollama at http://localhost:11434")

    monkeypatch.setattr(services.llm, "json", unavailable)
    result = services.overview.generate(sample_project)
    after = services.overview.get(sample_project)
    assert len(result["warnings"]) == 2
    assert all("Cannot reach Ollama" in w and "previous" in w for w in after["warnings"])
    assert after["summary"] == before["summary"]
    assert after["execution_flow"] == before["execution_flow"]
    assert [m["description"] for m in after["modules"]] == [m["description"] for m in before["modules"]]


def test_overview_without_previous_text_reports_the_failure(services, sample_project, monkeypatch):
    def unavailable(*args, **kwargs):
        raise OllamaError("Cannot reach Ollama")

    monkeypatch.setattr(services.llm, "json", unavailable)
    services.overview.generate(sample_project)
    overview = services.overview.get(sample_project)
    assert overview["summary"] == "" and overview["execution_flow"] == []
    assert overview["warnings"] == ["Summary could not be generated: Cannot reach Ollama",
                                    "Execution flow could not be generated: Cannot reach Ollama"]
    assert overview["mermaid"].startswith("flowchart LR")  # the static parts are still produced


def interrupted_index(services, project_id: int) -> int:
    """The state a crash mid-index leaves behind."""
    services.db.execute("UPDATE projects SET status = 'indexing' WHERE id = ?", (project_id,))
    return services.db.execute(
        "INSERT INTO jobs (project_id, kind, status, phase, created_at) VALUES (?, 'index', 'running', 'embedding', '')",
        (project_id,),
    )


def test_server_start_releases_projects_stuck_in_indexing(services, sample_project, monkeypatch):
    from fastapi.testclient import TestClient

    from repoguide.api import app as app_module

    other = services.projects.create(str(services.settings.eval_dir))  # registered, never indexed
    job_id = interrupted_index(services, sample_project)
    monkeypatch.setattr(app_module, "build_services", lambda: services)
    with TestClient(app_module.create_app()):  # the server's startup path
        project = services.projects.get(sample_project)
        assert project["status"] == "error" and "restarted" in project["error"]
        assert services.jobs.get(job_id)["status"] == "error"
        assert project["chunk_count"] > 0  # the existing index is kept and still answers questions
        assert services.projects.get(other["id"])["status"] == "new"  # projects that were not indexing are untouched


def test_scripts_sharing_the_database_leave_live_jobs_alone(services, sample_project):
    job_id = interrupted_index(services, sample_project)
    JobManager(services.db).shutdown()  # what evaluate.py --in-process builds while the server may be running
    assert services.projects.get(sample_project)["status"] == "indexing"
    assert services.jobs.get(job_id)["status"] == "running"


def test_mermaid_is_empty_without_modules():
    assert build_mermaid([], {}, [], [], {}, []) == ""


def test_project_with_error_status_can_still_answer(client, services, sample_project):
    services.db.execute("UPDATE projects SET status = 'error', error = 'interrupted' WHERE id = ?", (sample_project,))
    events = ask(client, sample_project, "How is tax calculated?")
    assert events[-1]["type"] == "done"
