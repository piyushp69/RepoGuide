"""Guards for the privacy guarantee: model calls only go to a local Ollama, and nothing else talks to the network."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from repoguide.config import Settings

PACKAGE = Path(__file__).resolve().parents[1] / "repoguide"
NETWORK_PACKAGES = {"httpx", "requests", "socket", "aiohttp", "websockets", "subprocess"}
NETWORK_MODULES = {"urllib.request", "http.client"}
ALLOWED = {
    "httpx": {"llm/ollama.py"},  # the Ollama client
    "subprocess": {"ingest/sources.py"},  # git clone / fetch of a repository the user added
}


@pytest.mark.parametrize("url", ["http://example.com:11434", "http://192.168.1.20:11434", "https://api.openai.com"])
def test_remote_ollama_urls_are_refused(url):
    with pytest.raises(ValidationError, match="must point to this machine"):
        Settings(ollama_base_url=url)


@pytest.mark.parametrize("url", ["http://localhost:11434", "http://127.0.0.1:11434", "http://[::1]:11434"])
def test_local_ollama_urls_are_accepted(url):
    assert Settings(ollama_base_url=url).ollama_base_url.rstrip("/") == url


def test_only_the_ollama_client_and_git_helper_use_the_network():
    found: dict[str, set[str]] = {}
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
            for name in names:
                if name in NETWORK_MODULES or name.split(".")[0] in NETWORK_PACKAGES:
                    found.setdefault(name, set()).add(path.relative_to(PACKAGE).as_posix())
    assert found == ALLOWED


def test_chroma_telemetry_is_off(tmp_path):
    from repoguide.index.vectorstore import VectorStore

    store = VectorStore(tmp_path / "chroma")
    assert store._client.get_settings().anonymized_telemetry is False
