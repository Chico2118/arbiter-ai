"""Provider adapters. Each one maps an endpoint snapshot + prompt to a request
following the provider's documented HTTP API, and maps the reply back to a
``Completion``.

Only the Ollama adapter has been exercised against a real server in this
project. The OpenAI, Anthropic, Gemini and OpenAI-compatible adapters are
written to the public API docs and covered by tests with mocked HTTP only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from . import config

DEFAULT_URLS = {
    "openai": "https://api.openai.com",
    "anthropic": "https://api.anthropic.com",
    "gemini": "https://generativelanguage.googleapis.com",
}
COMPANY = {
    "ollama": "Local (Ollama on this machine)",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "gemini": "Google",
    "openai_compatible": "Third-party OpenAI-compatible server",
}
NEEDS_KEY = {"openai", "anthropic", "gemini"}
ANTHROPIC_VERSION = "2023-06-01"

# Tests replace this with httpx.MockTransport so no real network call happens.
_transport: httpx.BaseTransport | None = None


def set_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport
    _transport = transport


def client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), transport=_transport)


@dataclass
class Completion:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    truncated: bool = False  # the provider stopped because of the max-token limit


class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False, status: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def base_url(snap: dict) -> str:
    if snap["provider"] == "ollama":
        return (snap.get("base_url") or config.settings.ollama_url).rstrip("/")
    return (snap.get("base_url") or DEFAULT_URLS.get(snap["provider"], "")).rstrip("/")


def is_local(snap: dict) -> bool:
    if snap["provider"] == "ollama":
        return True
    url = base_url(snap)
    return any(h in url for h in ("://localhost", "://127.0.0.1", "://[::1]"))


def destination(snap: dict) -> dict[str, Any]:
    url = base_url(snap)
    host = url.split("://", 1)[-1].split("/", 1)[0]
    return {"company": COMPANY.get(snap["provider"], snap["provider"]), "host": host,
            "local": is_local(snap)}


def family(model: str, provider: str) -> str:
    """Model family, used to stop a judge from grading its own family."""
    m = model.lower()
    for needle, fam in (("claude", "anthropic"), ("gemini", "google"), ("gemma", "google"),
                        ("gpt", "openai"), ("o1", "openai"), ("o3", "openai"),
                        ("o4", "openai"), ("qwen", "qwen"), ("llama", "llama"),
                        ("mistral", "mistral"), ("mixtral", "mistral"), ("phi", "phi"),
                        ("deepseek", "deepseek")):
        if needle in m:
            return fam
    return {"openai": "openai", "anthropic": "anthropic", "gemini": "google"}.get(provider, m)


def _raise_for(resp: httpx.Response, provider: str) -> None:
    if resp.status_code < 400:
        return
    retryable = resp.status_code == 429 or resp.status_code >= 500
    # Only the status and a short, key-free reason go into the error message.
    try:
        body = resp.json()
        detail = body.get("error", body) if isinstance(body, dict) else body
        if isinstance(detail, dict):
            detail = detail.get("message") or detail.get("type") or ""
    except ValueError:
        detail = resp.text
    detail = str(detail)[:200]
    raise ProviderError(f"{provider} returned HTTP {resp.status_code}: {detail}",
                        retryable, resp.status_code)


def _messages(snap: dict, prompt: str) -> list[dict]:
    msgs = []
    if snap.get("system_prompt"):
        msgs.append({"role": "system", "content": snap["system_prompt"]})
    msgs.append({"role": "user", "content": prompt})
    return msgs


# --------------------------------------------------------------------------
# Adapters
# --------------------------------------------------------------------------

def _ollama(c: httpx.Client, snap: dict, prompt: str, key: str | None) -> Completion:
    body: dict[str, Any] = {
        "model": snap["model"],
        "messages": _messages(snap, prompt),
        "stream": False,
        "options": {"temperature": snap["temperature"], "num_predict": snap["max_tokens"]},
    }
    if snap.get("think") is not None:
        body["think"] = bool(snap["think"])
    t0 = time.perf_counter()
    r = c.post(f"{base_url(snap)}/api/chat", json=body)
    _raise_for(r, "Ollama")
    data = r.json()
    return Completion(
        text=(data.get("message") or {}).get("content", ""),
        input_tokens=data.get("prompt_eval_count"),
        output_tokens=data.get("eval_count"),
        latency_ms=(time.perf_counter() - t0) * 1000,
        truncated=data.get("done_reason") == "length",
    )


def _openai_like(c: httpx.Client, snap: dict, prompt: str, key: str | None,
                 official: bool) -> Completion:
    body: dict[str, Any] = {
        "model": snap["model"],
        "messages": _messages(snap, prompt),
        "temperature": snap["temperature"],
    }
    # OpenAI's Chat Completions API now documents max_completion_tokens;
    # most OpenAI-compatible servers still expect max_tokens.
    body["max_completion_tokens" if official else "max_tokens"] = snap["max_tokens"]
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    url = base_url(snap)
    path = "/v1/chat/completions" if not url.endswith("/v1") else "/chat/completions"
    t0 = time.perf_counter()
    r = c.post(url + path, json=body, headers=headers)
    _raise_for(r, "OpenAI" if official else "OpenAI-compatible server")
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    usage = data.get("usage") or {}
    return Completion(
        text=(choice.get("message") or {}).get("content") or "",
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        latency_ms=(time.perf_counter() - t0) * 1000,
        truncated=choice.get("finish_reason") == "length",
    )


def _anthropic(c: httpx.Client, snap: dict, prompt: str, key: str | None) -> Completion:
    body: dict[str, Any] = {
        "model": snap["model"],
        "max_tokens": snap["max_tokens"],
        "messages": [{"role": "user", "content": prompt}],
        "temperature": snap["temperature"],
    }
    if snap.get("system_prompt"):
        body["system"] = snap["system_prompt"]
    headers = {"x-api-key": key or "", "anthropic-version": ANTHROPIC_VERSION,
               "content-type": "application/json"}
    t0 = time.perf_counter()
    r = c.post(f"{base_url(snap)}/v1/messages", json=body, headers=headers)
    _raise_for(r, "Anthropic")
    data = r.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    usage = data.get("usage") or {}
    return Completion(text, usage.get("input_tokens"), usage.get("output_tokens"),
                      (time.perf_counter() - t0) * 1000,
                      truncated=data.get("stop_reason") == "max_tokens")


def _gemini(c: httpx.Client, snap: dict, prompt: str, key: str | None) -> Completion:
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": snap["temperature"],
                             "maxOutputTokens": snap["max_tokens"]},
    }
    if snap.get("system_prompt"):
        body["systemInstruction"] = {"parts": [{"text": snap["system_prompt"]}]}
    # The key goes in a header, not the URL, so it never shows up in URL logs.
    headers = {"x-goog-api-key": key or ""}
    url = f"{base_url(snap)}/v1beta/models/{snap['model']}:generateContent"
    t0 = time.perf_counter()
    r = c.post(url, json=body, headers=headers)
    _raise_for(r, "Gemini")
    data = r.json()
    cands = data.get("candidates") or [{}]
    parts = (cands[0].get("content") or {}).get("parts") or []
    usage = data.get("usageMetadata") or {}
    return Completion("".join(p.get("text", "") for p in parts),
                      usage.get("promptTokenCount"), usage.get("candidatesTokenCount"),
                      (time.perf_counter() - t0) * 1000,
                      truncated=cands[0].get("finishReason") == "MAX_TOKENS")


ADAPTERS: dict[str, Callable[..., Completion]] = {
    "ollama": _ollama,
    "openai": lambda c, s, p, k: _openai_like(c, s, p, k, official=True),
    "openai_compatible": lambda c, s, p, k: _openai_like(c, s, p, k, official=False),
    "anthropic": _anthropic,
    "gemini": _gemini,
}


def complete(snap: dict, prompt: str, api_key: str | None,
             sleep: Callable[[float], None] = time.sleep) -> Completion:
    """Call the provider with retries + exponential backoff on retryable errors."""
    if snap["provider"] in NEEDS_KEY and not api_key:
        raise ProviderError("No API key stored for this endpoint")
    attempts = config.settings.retry_attempts
    delay = config.settings.retry_base_delay
    last: ProviderError | None = None
    for attempt in range(attempts):
        try:
            with client(float(snap.get("timeout_s") or 300)) as c:
                return ADAPTERS[snap["provider"]](c, snap, prompt, api_key)
        except ProviderError as exc:
            last = exc
        except httpx.TimeoutException:
            last = ProviderError(f"Request timed out after {snap.get('timeout_s')}s", True)
        except httpx.TransportError:
            where = "Ollama" if snap["provider"] == "ollama" else "the provider"
            hint = " Is `ollama serve` running?" if snap["provider"] == "ollama" else ""
            last = ProviderError(f"Could not connect to {where} at {base_url(snap)}.{hint}", True)
        except (ValueError, KeyError, TypeError) as exc:
            last = ProviderError(f"Unexpected response format: {type(exc).__name__}")
        if not last.retryable or attempt == attempts - 1:
            break
        sleep(delay * (2 ** attempt))
    assert last is not None
    raise last


def test_connection(snap: dict, api_key: str | None) -> dict[str, Any]:
    """Cheap connectivity check that does not spend tokens (model-list endpoints)."""
    prov = snap["provider"]
    if prov in NEEDS_KEY and not api_key:
        return {"ok": False, "message": "No API key stored for this endpoint"}
    url = base_url(snap)
    try:
        with client(10.0) as c:
            if prov == "ollama":
                r = c.get(f"{url}/api/tags")
                _raise_for(r, "Ollama")
                names = [m.get("name") for m in r.json().get("models", [])]
                if snap["model"] not in names and f"{snap['model']}:latest" not in names:
                    return {"ok": False, "message": f"Ollama is up, but model '{snap['model']}' "
                            f"is not pulled. Available: {', '.join(names) or 'none'}"}
                return {"ok": True, "message": f"Ollama reachable; '{snap['model']}' is available"}
            if prov in ("openai", "openai_compatible"):
                path = "/models" if url.endswith("/v1") else "/v1/models"
                headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
                r = c.get(url + path, headers=headers)
                _raise_for(r, "Server")
            elif prov == "anthropic":
                r = c.get(f"{url}/v1/models", headers={"x-api-key": api_key or "",
                                                        "anthropic-version": ANTHROPIC_VERSION})
                _raise_for(r, "Anthropic")
            elif prov == "gemini":
                r = c.get(f"{url}/v1beta/models", headers={"x-goog-api-key": api_key or ""})
                _raise_for(r, "Gemini")
            return {"ok": True, "message": "Connection and authentication succeeded"}
    except ProviderError as exc:
        return {"ok": False, "message": str(exc)}
    except httpx.TransportError:
        hint = " Is `ollama serve` running?" if prov == "ollama" else ""
        return {"ok": False, "message": f"Could not connect to {url}.{hint}"}


def estimate_tokens(text: str) -> int:
    """Rough token estimate (~4 characters per token) for cost previews."""
    return max(1, len(text) // 4)


def cost_usd(snap: dict, input_tokens: int | None, output_tokens: int | None) -> float:
    return ((input_tokens or 0) * float(snap.get("price_in") or 0)
            + (output_tokens or 0) * float(snap.get("price_out") or 0)) / 1_000_000
