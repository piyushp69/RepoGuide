"""HTTP client for the local Ollama server (chat, streaming chat, JSON output, embeddings)."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator

import httpx

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    """Ollama is unreachable, the model is missing, or the response was unusable."""


# Appended when generation stops at the num_predict limit, so a cut-off answer never looks complete.
TRUNCATION_NOTE = "\n\n_(The answer was cut off at the length limit.)_"


def _model_matches(wanted: str, installed: str) -> bool:
    if ":" not in wanted:
        wanted += ":latest"
    if ":" not in installed:
        installed += ":latest"
    return wanted == installed


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 600.0, keep_alive: str = "30m"):
        self.base_url = base_url.rstrip("/")
        self.keep_alive = keep_alive
        self._http = httpx.Client(base_url=self.base_url, timeout=httpx.Timeout(timeout, connect=5.0))

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = self._http.post(path, json=payload)
        except httpx.ConnectError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}. Is `ollama serve` running?") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(f"Ollama did not answer in time ({path}).") from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"Ollama request failed: {exc}") from exc
        if response.status_code != 200:
            raise OllamaError(_error_text(response))
        return response

    def list_models(self) -> list[str]:
        try:
            response = self._http.get("/api/tags", timeout=5.0)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        return [m.get("name", "") for m in response.json().get("models", [])]

    def health(self, chat_model: str, embed_model: str) -> dict[str, Any]:
        try:
            models = self.list_models()
        except OllamaError as exc:
            return {"reachable": False, "error": str(exc), "models": [],
                    "chat_model_available": False, "embed_model_available": False}
        return {
            "reachable": True,
            "error": None,
            "models": models,
            "chat_model_available": any(_model_matches(chat_model, m) for m in models),
            "embed_model_available": any(_model_matches(embed_model, m) for m in models),
        }

    def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        response = self._post("/api/embed", {
            "model": model, "input": inputs, "truncate": True, "keep_alive": self.keep_alive,
        })
        vectors = response.json().get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(inputs):
            raise OllamaError("Ollama returned an unexpected embedding response.")
        return vectors

    def chat(self, model: str, messages: list[dict[str, str]], options: dict[str, Any] | None = None,
             fmt: Any = None) -> str:
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                                   "options": options or {}, "keep_alive": self.keep_alive}
        if fmt is not None:
            payload["format"] = fmt
        data = self._post("/api/chat", payload).json()
        content = (data.get("message") or {}).get("content", "")
        if fmt is None and data.get("done_reason") == "length":
            content += TRUNCATION_NOTE
        return content

    def chat_stream(self, model: str, messages: list[dict[str, str]],
                    options: dict[str, Any] | None = None) -> Iterator[str]:
        payload = {"model": model, "messages": messages, "stream": True,
                   "options": options or {}, "keep_alive": self.keep_alive}
        try:
            with self._http.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    response.read()
                    raise OllamaError(_error_text(response))
                for line in response.iter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise OllamaError(str(data["error"]))
                    piece = (data.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
                    if data.get("done"):
                        if data.get("done_reason") == "length":
                            yield TRUNCATION_NOTE
                        return
        except httpx.ConnectError as exc:
            raise OllamaError(f"Cannot reach Ollama at {self.base_url}. Is `ollama serve` running?") from exc
        except httpx.TimeoutException as exc:
            raise OllamaError("Ollama stopped responding while streaming.") from exc


def _error_text(response: httpx.Response) -> str:
    try:
        detail = response.json().get("error") or response.text
    except ValueError:
        detail = response.text
    return f"Ollama returned HTTP {response.status_code}: {str(detail)[:300]}"


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_text(raw: str) -> Any:
    """Parse model output as JSON, tolerating code fences or leading prose."""
    text = _FENCE.sub("", raw.strip()).strip()
    try:
        return json.loads(text)
    except ValueError:
        start = min([i for i in (text.find("{"), text.find("[")) if i >= 0], default=-1)
        end = max(text.rfind("}"), text.rfind("]"))
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


class ChatLLM:
    """The configured chat model with RepoGuide's default generation options."""

    def __init__(self, client: OllamaClient, model: str, num_ctx: int = 8192, temperature: float = 0.1):
        self.client = client
        self.model = model
        self.num_ctx = num_ctx
        self.temperature = temperature

    def _options(self, temperature: float | None, num_predict: int | None) -> dict[str, Any]:
        options: dict[str, Any] = {"num_ctx": self.num_ctx,
                                   "temperature": self.temperature if temperature is None else temperature}
        if num_predict:
            options["num_predict"] = num_predict
        return options

    def complete(self, messages: list[dict[str, str]], temperature: float | None = None,
                 num_predict: int | None = None) -> str:
        return self.client.chat(self.model, messages, self._options(temperature, num_predict))

    def stream(self, messages: list[dict[str, str]], temperature: float | None = None,
               num_predict: int | None = None) -> Iterator[str]:
        return self.client.chat_stream(self.model, messages, self._options(temperature, num_predict))

    def json(self, messages: list[dict[str, str]], schema: dict[str, Any], retries: int = 2,
             temperature: float | None = None, num_predict: int | None = 2048) -> Any:
        """Structured output constrained by a JSON schema, retried if the result does not parse."""
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            temp = temperature if attempt == 0 else 0.3 + 0.2 * attempt
            raw = self.client.chat(self.model, messages, self._options(temp, num_predict), fmt=schema)
            try:
                return parse_json_text(raw)
            except ValueError as exc:
                last_error = exc
                log.warning("Model returned invalid JSON (attempt %d): %.200s", attempt + 1, raw)
        raise OllamaError(f"The model did not return valid JSON: {last_error}")

    def health(self, embed_model: str) -> dict[str, Any]:
        return self.client.health(self.model, embed_model)
