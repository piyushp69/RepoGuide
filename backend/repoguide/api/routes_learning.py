"""Learners, onboarding paths, quizzes, progress and the manager dashboard."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..onboarding.roles import ROLES
from ..services import Services
from .deps import require_indexed, require_project, services

router = APIRouter(prefix="/api")


class LearnerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)


class ProgressUpdate(BaseModel):
    learner: str = Field(..., min_length=1, max_length=60)
    step_index: int = Field(..., ge=0)
    completed: bool = True


class QuizSubmission(BaseModel):
    learner: str = Field(..., min_length=1, max_length=60)
    answers: list[int | None]


def _role(role: str) -> str:
    if role not in ROLES:
        raise HTTPException(status_code=404, detail=f"Unknown role. Use one of: {', '.join(ROLES)}")
    return role


def _path(s: Services, path_id: int) -> dict[str, Any]:
    row = s.db.query_one("SELECT id, project_id, role FROM learning_paths WHERE id = ?", (path_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Learning path not found")
    return row


@router.get("/learners")
def learners(s: Services = Depends(services)) -> list[dict[str, Any]]:
    return s.onboarding.learners()


@router.post("/learners", status_code=201)
def add_learner(body: LearnerCreate, s: Services = Depends(services)) -> dict[str, Any]:
    return s.onboarding.ensure_learner(body.name)


@router.get("/roles")
def roles() -> list[dict[str, str]]:
    return [{"id": key, **value} for key, value in ROLES.items()]


@router.get("/projects/{project_id}/onboarding/{role}")
def get_path(project_id: int, role: str, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    role = _role(role)
    return {"path": s.onboarding.latest_path(project_id, role),
            "job": s.jobs.latest(project_id, "onboarding", key=role)}


@router.post("/projects/{project_id}/onboarding/{role}")
def generate_path(project_id: int, role: str, s: Services = Depends(services)) -> dict[str, Any]:
    require_indexed(s, project_id)
    role = _role(role)
    return s.jobs.submit("onboarding", lambda reporter: s.onboarding.generate_path(project_id, role, reporter),
                         project_id=project_id, key=role)


@router.get("/paths/{path_id}/progress")
def get_progress(path_id: int, learner: str, s: Services = Depends(services)) -> dict[str, Any]:
    _path(s, path_id)
    return s.onboarding.progress(learner, path_id)


@router.post("/paths/{path_id}/progress")
def set_progress(path_id: int, body: ProgressUpdate, s: Services = Depends(services)) -> dict[str, Any]:
    _path(s, path_id)
    s.onboarding.set_step(body.learner, path_id, body.step_index, body.completed)
    return s.onboarding.progress(body.learner, path_id)


@router.get("/paths/{path_id}/steps/{step_index}/quiz")
def get_quiz(path_id: int, step_index: int, s: Services = Depends(services)) -> dict[str, Any]:
    path = _path(s, path_id)
    return {"quiz": s.onboarding.quiz_for_step(path_id, step_index),
            "job": s.jobs.latest(path["project_id"], "quiz", key=f"{path_id}:{step_index}")}


@router.post("/paths/{path_id}/steps/{step_index}/quiz")
def generate_quiz(path_id: int, step_index: int, s: Services = Depends(services)) -> dict[str, Any]:
    path = _path(s, path_id)
    return s.jobs.submit("quiz", lambda reporter: s.onboarding.generate_quiz(path_id, step_index, reporter),
                         project_id=path["project_id"], key=f"{path_id}:{step_index}")


@router.post("/quizzes/{quiz_id}/attempts")
def submit_quiz(quiz_id: int, body: QuizSubmission, s: Services = Depends(services)) -> dict[str, Any]:
    return s.onboarding.submit_quiz(quiz_id, body.learner, body.answers)


@router.get("/manager/dashboard")
def dashboard(project_id: int | None = None, s: Services = Depends(services)) -> dict[str, Any]:
    if project_id is not None:
        require_project(s, project_id)
    return s.onboarding.dashboard(project_id)
