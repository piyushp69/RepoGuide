"""Projects, indexing jobs, files, overview and search."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..services import Services
from .deps import require_indexed, require_project, services

router = APIRouter(prefix="/api")


class ProjectCreate(BaseModel):
    source: str = Field(..., min_length=1, max_length=1000, description="Local folder path or GitHub URL")
    name: str | None = Field(None, max_length=120)
    index: bool = True


class ProjectUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class IndexRequest(BaseModel):
    full: bool = False


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["hybrid", "vector", "bm25"] = "hybrid"
    top_k: int = Field(8, ge=1, le=50)
    expand: bool = True


@router.get("/ping")
def ping() -> dict[str, str]:
    """Liveness check that touches nothing else (used by scripts to detect a running backend)."""
    return {"status": "ok", "service": "repoguide"}


@router.get("/health")
def health(s: Services = Depends(services)) -> dict[str, Any]:
    from .. import __version__

    return {
        "status": "ok",
        "version": __version__,
        "ollama": s.ollama.health(s.settings.chat_model, s.settings.embed_model),
        "config": {"chat_model": s.settings.chat_model, "embed_model": s.settings.embed_model,
                   "ollama_base_url": s.settings.ollama_base_url, "num_ctx": s.settings.llm_num_ctx},
    }


@router.get("/projects")
def list_projects(s: Services = Depends(services)) -> list[dict[str, Any]]:
    return s.projects.list()


@router.post("/projects", status_code=201)
def create_project(body: ProjectCreate, s: Services = Depends(services)) -> dict[str, Any]:
    project = s.projects.create(body.source, body.name)
    job = s.projects.start_index(project["id"]) if body.index else None
    return {"project": s.projects.get(project["id"]), "job": job}


@router.get("/projects/{project_id}")
def get_project(project_id: int, s: Services = Depends(services)) -> dict[str, Any]:
    return require_project(s, project_id)


@router.patch("/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    return s.projects.rename(project_id, body.name)  # type: ignore[return-value]


@router.delete("/projects/{project_id}")
def delete_project(project_id: int, s: Services = Depends(services)) -> dict[str, bool]:
    require_project(s, project_id)
    return {"deleted": s.projects.delete(project_id)}


@router.post("/projects/{project_id}/index")
def index_project(project_id: int, body: IndexRequest | None = None, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    return s.projects.start_index(project_id, full=bool(body and body.full))


@router.get("/projects/{project_id}/jobs")
def project_jobs(project_id: int, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    latest = {kind: s.jobs.latest(project_id, kind) for kind in ("index", "overview")}
    return {"active": s.jobs.active(project_id), "latest": latest}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, s: Services = Depends(services)) -> dict[str, Any]:
    job = s.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("/projects/{project_id}/files")
def project_files(project_id: int, s: Services = Depends(services)) -> list[dict[str, Any]]:
    require_project(s, project_id)
    return s.projects.files(project_id)


@router.get("/projects/{project_id}/symbols")
def project_symbols(project_id: int, path: str | None = None, s: Services = Depends(services)) -> list[dict[str, Any]]:
    require_project(s, project_id)
    return s.projects.symbols(project_id, path)


@router.get("/projects/{project_id}/file")
def read_file(project_id: int, path: str, start: int | None = Query(None, ge=1), end: int | None = Query(None, ge=1),
              context: int = Query(0, ge=0, le=200), s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    return s.projects.read_file(project_id, path, start, end, context)


@router.get("/projects/{project_id}/chunks/{chunk_id}/related")
def related(project_id: int, chunk_id: str, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    return s.projects.related(project_id, chunk_id)


@router.post("/projects/{project_id}/search")
def search(project_id: int, body: SearchRequest, s: Services = Depends(services)) -> dict[str, Any]:
    require_indexed(s, project_id)
    result = s.retriever.retrieve(project_id, body.query, top_k=body.top_k, mode=body.mode, expand=body.expand)
    return {"query": result.query, "search_query": result.search_query, "seed_count": result.seed_count,
            "timings_ms": result.timings_ms, "results": [{**c.summary(), "content": c.content} for c in result.chunks]}


@router.get("/projects/{project_id}/overview")
def get_overview(project_id: int, s: Services = Depends(services)) -> dict[str, Any]:
    require_project(s, project_id)
    overview = s.overview.get(project_id)
    job = s.jobs.latest(project_id, "overview")
    index_job = s.jobs.latest(project_id, "index")
    return {"overview": overview, "job": job, "index_job": index_job}


@router.post("/projects/{project_id}/overview")
def regenerate_overview(project_id: int, s: Services = Depends(services)) -> dict[str, Any]:
    require_indexed(s, project_id)
    return s.jobs.submit("overview", lambda reporter: s.overview.generate(project_id, reporter), project_id=project_id)
