"""The Ollama client against a mocked HTTP transport (no server needed)."""

from __future__ import annotations

import json

import httpx
import pytest

from repoguide.llm.ollama import TRUNCATION_NOTE, ChatLLM, OllamaClient, OllamaError


def client_with(handler) -> OllamaClient:
    client = OllamaClient("http://localhost:11434")
    client._http = httpx.Client(base_url="http://localhost:11434", transport=httpx.MockTransport(handler))
    return client


def test_chat_marks_answers_cut_off_at_the_length_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "Partial answer"}, "done": True,
                                         "done_reason": "length"})

    llm = ChatLLM(client_with(handler), "test-model", num_ctx=4096, temperature=0.1)
    assert llm.complete([{"role": "user", "content": "hi"}], temperature=0.0, num_predict=64) == \
        "Partial answer" + TRUNCATION_NOTE
    assert seen["options"] == {"num_ctx": 4096, "temperature": 0.0, "num_predict": 64}
    assert seen["stream"] is False


def test_stream_yields_pieces_and_truncation_note():
    lines = [{"message": {"content": "Hello "}, "done": False}, {"message": {"content": "world"}, "done": False},
             {"message": {"content": ""}, "done": True, "done_reason": "length"}]
    body = "\n".join(json.dumps(line) for line in lines)
    llm = ChatLLM(client_with(lambda request: httpx.Response(200, text=body)), "test-model")
    assert list(llm.stream([{"role": "user", "content": "hi"}])) == ["Hello ", "world", TRUNCATION_NOTE]


def test_errors_and_json_output():
    missing = client_with(lambda request: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(OllamaError, match="not found"):
        missing.chat("x", [{"role": "user", "content": "hi"}])
    fenced = '```json\n{"summary": "ok", "modules": []}\n```'
    llm = ChatLLM(client_with(lambda request: httpx.Response(200, json={"message": {"content": fenced},
                                                                        "done": True, "done_reason": "stop"})), "m")
    assert llm.json([{"role": "user", "content": "x"}], {"type": "object"}) == {"summary": "ok", "modules": []}
    health = client_with(lambda request: httpx.Response(200, json={"models": [{"name": "nomic-embed-text:latest"}]}))
    status = health.health("qwen2.5-coder:7b", "nomic-embed-text")
    assert status["reachable"] and status["embed_model_available"] and not status["chat_model_available"]
