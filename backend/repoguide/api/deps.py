"""Request-scoped helpers shared by the routers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ..services import Services


def services(request: Request) -> Services:
    return request.app.state.services


def require_project(s: Services, project_id: int) -> dict[str, Any]:
    project = s.projects.get(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_indexed(s: Services, project_id: int) -> dict[str, Any]:
    project = require_project(s, project_id)
    if not project["chunk_count"]:
        if project["status"] == "indexing":
            detail = "This project is still being indexed. Try again when indexing finishes."
        elif project["status"] == "ready":
            detail = "This project was indexed but contains no supported source files to search."
        else:
            detail = "This project has not been indexed yet."
        raise HTTPException(status_code=409, detail=detail)
    return project
