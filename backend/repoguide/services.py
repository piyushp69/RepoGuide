"""Wires the application components together (one instance per process or test)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings, get_settings
from .db import Database
from .index.bm25 import BM25Cache
from .index.indexer import Indexer
from .index.vectorstore import VectorStore
from .jobs import JobManager
from .llm.embedder import Embedder, OllamaEmbedder
from .llm.ollama import ChatLLM, OllamaClient
from .qa.service import QAService
from .retrieval.hybrid import HybridRetriever


@dataclass
class Services:
    settings: Settings
    db: Database
    ollama: OllamaClient
    llm: Any  # ChatLLM-compatible
    embedder: Embedder
    store: VectorStore
    bm25: BM25Cache
    retriever: HybridRetriever
    jobs: JobManager
    indexer: Indexer
    qa: QAService
    overview: Any = None
    onboarding: Any = None
    projects: Any = None
    feedback: Any = None
    evaluation: Any = None

    def close(self) -> None:
        self.jobs.shutdown()
        self.ollama.close()


def build_services(settings: Settings | None = None, *, llm: Any = None, embedder: Embedder | None = None,
                   job_workers: int = 2) -> Services:
    """Create all services. Tests pass fake ``llm``/``embedder`` objects to run without Ollama."""
    from .evaluation.service import EvaluationService
    from .feedback.service import FeedbackService
    from .onboarding.service import OnboardingService
    from .overview.service import OverviewService
    from .projects import ProjectService

    settings = settings or get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    ollama = OllamaClient(settings.ollama_base_url, settings.llm_timeout_seconds, settings.ollama_keep_alive)
    if llm is None:
        llm = ChatLLM(ollama, settings.chat_model, settings.llm_num_ctx, settings.llm_temperature)
    if embedder is None:
        doc_prefix, query_prefix = settings.resolved_embed_prefixes()
        embedder = OllamaEmbedder(ollama, settings.embed_model, doc_prefix, query_prefix,
                                  settings.embed_batch_size, settings.embed_max_chars)
    store = VectorStore(settings.chroma_dir)
    bm25 = BM25Cache(db)
    retriever = HybridRetriever(settings, db, store, embedder, bm25)
    jobs = JobManager(db, max_workers=job_workers)
    indexer = Indexer(settings, db, store, embedder, bm25)
    qa = QAService(settings, db, retriever, llm)
    services = Services(settings=settings, db=db, ollama=ollama, llm=llm, embedder=embedder, store=store,
                        bm25=bm25, retriever=retriever, jobs=jobs, indexer=indexer, qa=qa)
    services.overview = OverviewService(services)
    services.onboarding = OnboardingService(services)
    services.projects = ProjectService(services)
    services.feedback = FeedbackService(services)
    services.evaluation = EvaluationService(services)
    return services
