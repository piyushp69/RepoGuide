"""Runtime configuration.

Values come from environment variables or the repository-level ``.env`` file, so
models, limits and paths can be changed without code edits.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Chroma reads this at import time. Product telemetry must never leave the machine.
os.environ["ANONYMIZED_TELEMETRY"] = "False"

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Ollama (local only) ---
    ollama_base_url: str = "http://localhost:11434"
    chat_model: str = "qwen2.5-coder:7b"
    embed_model: str = "nomic-embed-text"
    # "auto" picks the task prefixes the embedding model expects (nomic-embed-text uses them).
    embed_doc_prefix: str = "auto"
    embed_query_prefix: str = "auto"
    llm_num_ctx: int = 8192
    llm_temperature: float = 0.1  # overview, onboarding paths and quizzes
    qa_temperature: float = 0.0  # answers: deterministic decoding stays closest to the excerpts
    # Hard cap on answer length. Without it a degenerate (repeating) generation never stops: Ollama
    # keeps shifting the context window until the request times out.
    qa_max_tokens: int = 1024
    llm_timeout_seconds: float = 300.0
    ollama_keep_alive: str = "30m"
    embed_batch_size: int = 16
    embed_max_chars: int = 6000

    # --- Storage ---
    data_dir: Path = Path("data")
    eval_dir: Path = Path("eval")

    # --- Ingestion ---
    max_file_size_kb: int = 512
    max_files: int = 5000
    git_timeout_seconds: int = 600

    # --- Chunking ---
    chunk_max_lines: int = 120
    class_inline_max_lines: int = 60
    window_lines: int = 60
    window_overlap: int = 10

    # --- Retrieval ---
    retrieval_top_k: int = 8
    retrieval_candidates: int = 25
    graph_expand_seeds: int = 4
    graph_max_neighbors: int = 6
    context_char_budget: int = 16000
    chat_history_turns: int = 2

    # --- Server ---
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_port: int = 5173

    @field_validator("ollama_base_url")
    @classmethod
    def _ollama_must_be_local(cls, value: str) -> str:
        host = urlparse(value).hostname
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                f"OLLAMA_BASE_URL must point to this machine (localhost); got host {host!r}. "
                "RepoGuide never sends code to remote model servers."
            )
        return value.rstrip("/")

    @model_validator(mode="after")
    def _resolve_paths(self) -> "Settings":
        if not self.data_dir.is_absolute():
            self.data_dir = (PROJECT_ROOT / self.data_dir).resolve()
        if not self.eval_dir.is_absolute():
            self.eval_dir = (PROJECT_ROOT / self.eval_dir).resolve()
        return self

    @property
    def db_path(self) -> Path:
        return self.data_dir / "repoguide.db"

    @property
    def chroma_dir(self) -> Path:
        return self.data_dir / "chroma"

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def eval_results_dir(self) -> Path:
        return self.eval_dir / "results"

    @property
    def sample_repo_dir(self) -> Path:
        return self.eval_dir / "sample_repo"

    @property
    def eval_set_path(self) -> Path:
        return self.eval_dir / "eval_set.json"

    @property
    def max_file_bytes(self) -> int:
        return self.max_file_size_kb * 1024

    @property
    def frontend_dist(self) -> Path:
        return PROJECT_ROOT / "frontend" / "dist"

    def resolved_embed_prefixes(self) -> tuple[str, str]:
        """Return (document_prefix, query_prefix) for the configured embedding model."""
        doc, query = self.embed_doc_prefix, self.embed_query_prefix
        if doc == "auto" or query == "auto":
            if "nomic-embed" in self.embed_model:
                auto_doc, auto_query = "search_document: ", "search_query: "
            elif "mxbai-embed" in self.embed_model:
                auto_doc, auto_query = "", "Represent this sentence for searching relevant passages: "
            else:
                auto_doc, auto_query = "", ""
            doc = auto_doc if doc == "auto" else doc
            query = auto_query if query == "auto" else query
        return doc, query


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
