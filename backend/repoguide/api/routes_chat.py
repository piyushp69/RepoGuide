"""Chat (streamed answers), history and feedback."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from ..services import Services
from .deps import require_indexed, require_project, services

router = APIRouter(prefix="/api")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    learner: str | None = Field(None, max_length=60)


class FeedbackRequest(BaseModel):
    rating: int = Field(..., description="1 = thumbs up, -1 = thumbs down")
    comment: str | None = Field(None, max_length=2000)


class EvalFromFeedback(BaseModel):
    expected_file: str = Field(..., min_length=1)
    expected_symbol: str | None = None


@router.get("/projects/{project_id}/chat")
def chat_history(project_id: int, learner: str | None = None, s: Services = Depends(services)) -> list[dict[str, Any]]:
    require_project(s, project_id)
    return s.qa.history(project_id, learner)


@router.delete("/projects/{project_id}/chat")
def clear_chat(project_id: int, learner: str | None = None, s: Services = Depends(services)) -> dict[str, int]:
    require_project(s, project_id)
    return {"deleted": s.qa.clear_history(project_id, learner)}


@router.post("/projects/{project_id}/chat")
def ask(project_id: int, body: ChatRequest, s: Services = Depends(services)) -> StreamingResponse:
    require_indexed(s, project_id)
    learner = body.learner.strip() if body.learner and body.learner.strip() else None
    if learner:
        learner = s.onboarding.ensure_learner(learner)["name"]
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is empty")

    def stream() -> Iterator[str]:
        for event in s.qa.answer_stream(project_id, question, learner):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(stream(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/messages/{message_id}/feedback")
def give_feedback(message_id: int, body: FeedbackRequest, s: Services = Depends(services)) -> dict[str, Any]:
    return s.feedback.submit(message_id, body.rating, body.comment)


@router.get("/projects/{project_id}/feedback")
def list_feedback(project_id: int, rating: int | None = None, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    return {"items": s.feedback.list(project_id, rating), "stats": s.feedback.stats(project_id)}


@router.post("/feedback/{feedback_id}/eval")
def feedback_to_eval(feedback_id: int, body: EvalFromFeedback, s: Services = Depends(services)) -> dict[str, Any]:
    return s.feedback.add_to_eval(feedback_id, body.expected_file, body.expected_symbol)


@router.get("/projects/{project_id}/eval-questions")
def eval_questions(project_id: int, s: Services = Depends(services)) -> list[dict[str, Any]]:
    require_project(s, project_id)
    return s.feedback.eval_questions(project_id)


@router.delete("/eval-questions/{question_id}")
def delete_eval_question(question_id: int, s: Services = Depends(services)) -> dict[str, bool]:
    if not s.feedback.delete_eval_question(question_id):
        raise HTTPException(status_code=404, detail="Question not found")
    return {"deleted": True}


@router.get("/projects/{project_id}/feedback/export")
def export_feedback(project_id: int, s: Services = Depends(services)) -> Response:
    require_project(s, project_id)
    lines = [json.dumps(r, ensure_ascii=False) for r in s.feedback.training_pairs(project_id)]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return Response(
        content="\n".join(lines) + ("\n" if lines else ""),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f'attachment; filename="repoguide-feedback-p{project_id}-{stamp}.jsonl"'},
    )
