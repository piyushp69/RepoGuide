"""Integration tests against the real local Ollama models.

Skipped automatically when Ollama is not running or the configured models are not pulled.
"""

from __future__ import annotations

import math

import pytest

from repoguide.config import Settings
from repoguide.evaluation import metrics as m
from repoguide.ingest.walker import read_text
from repoguide.llm import OllamaClient, OllamaEmbedder
from repoguide.parsing.chunker import split_lines
from repoguide.services import build_services

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ollama_settings() -> Settings:
    settings = Settings()
    health = OllamaClient(settings.ollama_base_url, timeout=10).health(settings.chat_model, settings.embed_model)
    if not health["reachable"]:
        pytest.skip(f"Ollama not reachable: {health['error']}")
    if not (health["chat_model_available"] and health["embed_model_available"]):
        pytest.skip("Configured Ollama models are not pulled")
    return settings


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))


def test_real_embeddings_rank_related_code_higher(ollama_settings):
    doc_prefix, query_prefix = ollama_settings.resolved_embed_prefixes()
    embedder = OllamaEmbedder(OllamaClient(ollama_settings.ollama_base_url), ollama_settings.embed_model,
                              doc_prefix, query_prefix)
    docs = embedder.embed_documents([
        "def hash_password(password, salt):\n    return pbkdf2_hmac('sha256', password, salt, 100000)",
        "export function Cart() { return <ul className='cart'>{items.map(renderItem)}</ul>; }",
    ])
    query = embedder.embed_query("how are passwords hashed?")
    assert len(docs) == 2 and len(query) == len(docs[0]) > 100
    assert cosine(query, docs[0]) > cosine(query, docs[1])


def test_real_answer_has_valid_citations_and_abstains(ollama_settings, workdir):
    settings = ollama_settings.model_copy(update={"data_dir": workdir / "data", "eval_dir": workdir / "eval"})
    services = build_services(settings)
    try:
        project_id = services.evaluation.sample_project()
        root = services.settings.sample_repo_dir
        files = {f["path"]: split_lines(read_text(root / f["path"])[1]) for f in services.projects.files(project_id)}

        outcome = services.qa.run_once(project_id, "How is the final order total calculated?")
        assert outcome["citations"], outcome["answer"]
        assert not outcome["not_found"]
        for citation in outcome["citations"]:
            valid, reason = m.check_citation(citation, files)
            assert valid, (citation, reason)
        assert any(c["file_path"] == "backend/services/pricing.py" for c in outcome["citations"])

        missing = services.qa.run_once(project_id, "How does the app charge credit cards through Stripe?")
        assert missing["not_found"], missing["answer"]
    finally:
        services.close()
