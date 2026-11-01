"""Shared fixtures. Tests never call a real model: every HTTP request goes
through an httpx.MockTransport that emulates the provider APIs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import httpx
import pytest

from arbiter import config, providers, runner


@dataclass
class FakeLLM:
    """Emulates Ollama, OpenAI, Anthropic and Gemini endpoints.

    ``responder(model, system, prompt) -> str`` decides the reply text.
    Every request is recorded so tests can inspect exactly what was sent.
    """

    responder: Callable[[str, str, str], str] = lambda model, system, prompt: "Answer: 42"
    requests: list[httpx.Request] = field(default_factory=list)
    fail_next: list[int] = field(default_factory=list)  # HTTP statuses to return first
    offline: bool = False
    # Decides whether a reply is reported as cut off at the token limit.
    truncate: Callable[[str, str, str], bool] = lambda model, system, prompt: False

    def bodies(self) -> list[str]:
        return [r.content.decode("utf-8", "replace") for r in self.requests]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.offline:
            raise httpx.ConnectError("connection refused", request=request)
        if self.fail_next:
            return httpx.Response(self.fail_next.pop(0), json={"error": {"message": "busy"}})
        path = request.url.path
        if request.method == "GET":
            if path.endswith("/api/tags"):
                return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
            return httpx.Response(200, json={"data": [], "models": []})
        body = json.loads(request.content or b"{}")
        if path.endswith("/api/chat"):  # Ollama
            msgs = body["messages"]
            system = next((m["content"] for m in msgs if m["role"] == "system"), "")
            text = self.responder(body["model"], system, msgs[-1]["content"])
            cut = self.truncate(body["model"], system, msgs[-1]["content"])
            return httpx.Response(200, json={"message": {"role": "assistant", "content": text},
                                             "prompt_eval_count": 20, "eval_count": 10,
                                             "done_reason": "length" if cut else "stop"})
        if path.endswith("/chat/completions"):  # OpenAI + compatible
            msgs = body["messages"]
            system = next((m["content"] for m in msgs if m["role"] == "system"), "")
            text = self.responder(body["model"], system, msgs[-1]["content"])
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}],
                                             "usage": {"prompt_tokens": 20, "completion_tokens": 10}})
        if path.endswith("/v1/messages"):  # Anthropic
            text = self.responder(body["model"], body.get("system", ""), body["messages"][-1]["content"])
            return httpx.Response(200, json={"content": [{"type": "text", "text": text}],
                                             "usage": {"input_tokens": 20, "output_tokens": 10}})
        if ":generateContent" in path:  # Gemini
            model = path.split("/models/")[1].split(":")[0]
            system = (body.get("systemInstruction") or {}).get("parts", [{}])[0].get("text", "")
            text = self.responder(model, system, body["contents"][-1]["parts"][0]["text"])
            return httpx.Response(200, json={
                "candidates": [{"content": {"parts": [{"text": text}]}}],
                "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 10}})
        return httpx.Response(404, json={"error": "unknown path"})


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ARBITER_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ARBITER_SECRET_BACKEND", "memory")
    monkeypatch.setenv("ARBITER_RETRY_BASE_DELAY", "0")
    monkeypatch.setenv("ARBITER_RETRY_ATTEMPTS", "3")
    monkeypatch.setenv("ARBITER_OLLAMA_URL", "http://localhost:11434")
    config.reload_settings()
    runner.SYNC = True
    yield
    providers.set_transport(None)
    runner.SYNC = False


@pytest.fixture
def fake_llm():
    fake = FakeLLM()
    providers.set_transport(httpx.MockTransport(fake.handler))
    return fake


@pytest.fixture
def client(fake_llm):
    from fastapi.testclient import TestClient

    from arbiter.main import create_app

    with TestClient(create_app()) as c:
        yield c


def make_endpoint(client, name: str, system_prompt: str = "", provider: str = "ollama",
                  model: str = "qwen3:4b", **extra) -> dict:
    body = {"name": name, "provider": provider, "model": model, "system_prompt": system_prompt,
            "think": False if provider == "ollama" else None, **extra}
    r = client.post("/api/endpoints", json=body)
    assert r.status_code == 201, r.text
    return r.json()
