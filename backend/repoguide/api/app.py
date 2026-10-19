"""FastAPI application factory.

Run with ``python -m repoguide`` (or ``uvicorn repoguide.api.app:app``) from the
``backend`` folder. When ``frontend/dist`` exists, the built UI is served too.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from ..config import get_settings
from ..errors import ConflictError, InvalidInputError, NotFoundError
from ..llm.ollama import OllamaError
from ..services import Services, build_services
from . import routes_chat, routes_eval, routes_learning, routes_projects

log = logging.getLogger(__name__)


def create_app(services: Services | None = None) -> FastAPI:
    owns_services = services is None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.services is None:
            app.state.services = build_services()
            app.state.services.jobs.recover_interrupted()
        yield
        if owns_services and app.state.services is not None:
            app.state.services.close()

    app = FastAPI(title="RepoGuide", version="1.0.0", lifespan=lifespan,
                  description="Private, self-hosted codebase explainer and onboarding tool.")
    app.state.services = services
    settings = services.settings if services else get_settings()
    port = settings.frontend_port
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[f"http://localhost:{port}", f"http://127.0.0.1:{port}"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(OllamaError)
    async def _ollama_error(_: Request, exc: OllamaError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc) or "Not found"})

    @app.exception_handler(InvalidInputError)
    async def _bad_request(_: Request, exc: InvalidInputError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    for module in (routes_projects, routes_chat, routes_learning, routes_eval):
        app.include_router(module.router)

    dist_dir = settings.frontend_dist
    if dist_dir.is_dir() and (dist_dir / "index.html").is_file():
        index_file = dist_dir / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not found"})
            candidate = (dist_dir / full_path).resolve()
            if full_path and candidate.is_file() and dist_dir.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app


app = create_app()
