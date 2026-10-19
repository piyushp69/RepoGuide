"""Evaluation runs and results."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..services import Services
from .deps import require_indexed, services

router = APIRouter(prefix="/api/eval")


class EvalRunRequest(BaseModel):
    source: Literal["sample", "project"] = "sample"
    project_id: int | None = None
    with_llm: bool = True
    limit: int | None = Field(None, ge=1, le=500)


@router.get("/set")
def sample_set(s: Services = Depends(services)) -> dict[str, Any]:
    data = s.evaluation.load_sample_set()
    return {"name": data.get("name"), "description": data.get("description"),
            "questions": data.get("questions", []), "unanswerable": data.get("unanswerable", [])}


@router.get("/runs")
def runs(s: Services = Depends(services)) -> list[dict[str, Any]]:
    return s.evaluation.history()


@router.get("/runs/{run_id}")
def run_detail(run_id: str, s: Services = Depends(services)) -> dict[str, Any]:
    report = s.evaluation.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Evaluation run not found")
    return report


@router.get("/latest")
def latest(s: Services = Depends(services)) -> dict[str, Any] | None:
    history = s.evaluation.history()
    return s.evaluation.get(history[0]["run_id"]) if history else None


@router.get("/jobs")
def eval_jobs(s: Services = Depends(services)) -> list[dict[str, Any]]:
    rows = s.db.query("SELECT id FROM jobs WHERE kind = 'eval' ORDER BY id DESC LIMIT 5")
    return [j for j in (s.jobs.get(r["id"]) for r in rows) if j]


@router.post("/run")
def run(body: EvalRunRequest, s: Services = Depends(services)) -> dict[str, Any]:
    if body.source == "project":
        if body.project_id is None:
            raise HTTPException(status_code=422, detail="project_id is required for a project evaluation set")
        require_indexed(s, body.project_id)
        eval_set = s.evaluation.custom_set(body.project_id)
        if not eval_set["questions"]:
            raise HTTPException(status_code=409, detail="This project's evaluation set is empty. Add thumbs-down "
                                                        "questions from the Feedback page first.")

        def job(reporter):
            report = s.evaluation.run(eval_set, body.project_id, body.with_llm, body.limit, reporter)
            return {"run_id": report["run_id"], "metrics": report["metrics"]}

        return s.jobs.submit("eval", job, project_id=body.project_id, key="project")

    def sample_job(reporter):
        reporter.update(phase="indexing", message="Indexing the sample repository")
        project_id = s.evaluation.sample_project(reporter)
        report = s.evaluation.run(s.evaluation.load_sample_set(), project_id, body.with_llm, body.limit, reporter)
        return {"run_id": report["run_id"], "metrics": report["metrics"]}

    return s.jobs.submit("eval", sample_job, key="sample")
