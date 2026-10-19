"""Deterministic stand-ins for the Ollama models so unit and API tests run offline.

They are only used by the test-suite; the application always uses the real local models.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterator

import numpy as np

from repoguide.index.bm25 import tokenize
from repoguide.qa.citations import NOT_FOUND_MESSAGE


class FakeEmbedder:
    """Hashing-trick bag of (stemmed, identifier-split) words, L2-normalised."""

    model_name = "fake-embed"

    def __init__(self, dim: int = 256):
        self.dim = dim
        self.document_calls = 0

    def _vector(self, text: str) -> list[float]:
        vector = np.zeros(self.dim)
        for token in tokenize(text):
            digest = int(hashlib.md5(token.encode()).hexdigest(), 16)
            vector[digest % self.dim] += 1.0
        norm = np.linalg.norm(vector)
        if norm == 0:
            vector[0] = 1.0
            norm = 1.0
        return (vector / norm).tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += len(texts)
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class FakeLLM:
    """Answers by citing the first excerpt; produces schema-shaped JSON from the prompt."""

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages: list[dict[str, str]], temperature: float | None = None,
                 num_predict: int | None = None) -> str:
        self.calls.append(messages)
        # Like a real model, use the most recent message that carries the numbered excerpts.
        for message in reversed(messages):
            match = re.search(r"^\[1\] (\S+):(\d+)-(\d+)", message["content"], re.MULTILINE)
            if match:
                return f"The relevant code is in `{match.group(1)}` [1]."
        return NOT_FOUND_MESSAGE

    def stream(self, messages: list[dict[str, str]], temperature: float | None = None,
               num_predict: int | None = None) -> Iterator[str]:
        text = self.complete(messages, temperature=temperature, num_predict=num_predict)
        words = text.split(" ")
        for index, word in enumerate(words):
            yield word + (" " if index < len(words) - 1 else "")

    def json(self, messages: list[dict[str, str]], schema: dict[str, Any], retries: int = 2,
             temperature: float | None = None, num_predict: int | None = 2048) -> Any:
        self.calls.append(messages)
        prompt = messages[-1]["content"]
        props = schema.get("properties", {})
        if "summary" in props:
            modules = re.findall(r"^- (.+?): files \[", prompt, re.MULTILINE)
            return {"summary": "ShopLite is a small shop with a Flask API and a React checkout.",
                    "modules": [{"path": p, "description": f"Code that lives in {p}."} for p in modules]}
        if "questions" in props:
            blocks = re.findall(r"```\n(\S+?):(\d+)-(\d+)\n(.*?)```", prompt, re.DOTALL)
            questions = []
            for index, (path, _start, _end, code) in enumerate(blocks[:3]):
                evidence = next((ln.strip() for ln in code.split("\n") if len(ln.strip()) >= 8), "")
                questions.append({
                    "question": f"Question {index + 1}: which file contains `{evidence[:30]}`?",
                    "options": [path, f"other/{index}/a.py", f"other/{index}/b.py", f"other/{index}/c.py"],
                    "answer_index": 0, "explanation": f"It is defined in {path}.", "file": path,
                    "evidence": evidence,
                })
            return {"questions": questions}
        if "steps" in props:
            item_props = props["steps"]["items"]["properties"]
            if "goal" in item_props:
                paths = re.findall(r"^- (\S+) \(", prompt, re.MULTILINE)
                steps = [{"title": "Big picture", "goal": "Understand the project.",
                          "files": [{"path": p, "why": f"Shows {p}."} for p in paths[:2]]},
                         {"title": "Core code", "goal": "Understand the main logic.",
                          "files": [{"path": p, "why": f"Shows {p}."} for p in paths[2:5]]}]
                return {"title": "Test path", "overview": "A generated test path.", "steps": steps}
            paths = list(dict.fromkeys(re.findall(r"([\w./-]+\.(?:py|tsx?|jsx?)):\d+-\d+", prompt)))
            return {"steps": [{"title": "Start the app", "description": "The entry point runs.", "files": paths[:1]},
                              {"title": "Handle requests", "description": "Requests reach services.",
                               "files": paths[1:3]}]}
        return {}
