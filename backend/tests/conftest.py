from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from repoguide.config import PROJECT_ROOT, Settings
from repoguide.jobs import PrintReporter
from repoguide.services import build_services

from .fakes import FakeEmbedder, FakeLLM

SAMPLE_REPO = PROJECT_ROOT / "eval" / "sample_repo"
EVAL_SET = PROJECT_ROOT / "eval" / "eval_set.json"


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """A private copy of the sample repo and eval set, so tests never touch the real fixtures."""
    eval_dir = tmp_path / "eval"
    shutil.copytree(SAMPLE_REPO, eval_dir / "sample_repo")
    shutil.copy(EVAL_SET, eval_dir / "eval_set.json")
    return tmp_path


@pytest.fixture
def settings(workdir: Path) -> Settings:
    return Settings(data_dir=workdir / "data", eval_dir=workdir / "eval", embed_batch_size=8)


@pytest.fixture
def services(settings: Settings):
    built = build_services(settings, llm=FakeLLM(), embedder=FakeEmbedder())
    yield built
    built.close()


@pytest.fixture
def sample_project(services) -> int:
    """The sample repo registered and indexed (fake embeddings)."""
    project = services.projects.create(str(services.settings.sample_repo_dir))
    services.indexer.index_project(project["id"], PrintReporter())
    return project["id"]


@pytest.fixture
def client(services):
    from fastapi.testclient import TestClient

    from repoguide.api.app import create_app

    with TestClient(create_app(services)) as test_client:
        yield test_client
