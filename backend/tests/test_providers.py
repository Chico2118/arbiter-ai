"""Provider adapters against mocked HTTP. These verify request shape (URL,
headers, body) per the providers' public API docs and response parsing. They
do NOT prove the adapters work against the live paid services."""

import json

import httpx
import pytest

from arbiter import providers

BASE = {"system_prompt": "Be brief.", "temperature": 0.2, "max_tokens": 64, "think": None,
        "price_in": 1.0, "price_out": 2.0, "timeout_s": 30, "base_url": "", "endpoint_id": 1}


def snap(provider, model, **kw):
    return {**BASE, "provider": provider, "model": model, "name": "t", **kw}


def test_ollama_request_and_parse(fake_llm):
    out = providers.complete(snap("ollama", "qwen3:4b", think=False), "Hi", None)
    req = fake_llm.requests[-1]
    body = json.loads(req.content)
    assert str(req.url) == "http://localhost:11434/api/chat"
    assert body["think"] is False and body["stream"] is False
    assert body["options"] == {"temperature": 0.2, "num_predict": 64}
    assert body["messages"][0] == {"role": "system", "content": "Be brief."}
    assert out.text == "Answer: 42" and out.input_tokens == 20 and out.output_tokens == 10


def test_openai_request_and_parse(fake_llm):
    out = providers.complete(snap("openai", "gpt-4o-mini"), "Hi", "sk-test-000000000000")
    req = fake_llm.requests[-1]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-test-000000000000"
    assert body["max_completion_tokens"] == 64 and "max_tokens" not in body
    assert out.output_tokens == 10


def test_openai_compatible_uses_max_tokens_and_v1_base(fake_llm):
    providers.complete(snap("openai_compatible", "llama", base_url="http://localhost:8080/v1"), "Hi", None)
    req = fake_llm.requests[-1]
    assert str(req.url) == "http://localhost:8080/v1/chat/completions"
    assert json.loads(req.content)["max_tokens"] == 64
    assert "authorization" not in req.headers


def test_anthropic_request_and_parse(fake_llm):
    out = providers.complete(snap("anthropic", "claude-sonnet-4-5"), "Hi", "sk-ant-test-0000000")
    req = fake_llm.requests[-1]
    body = json.loads(req.content)
    assert str(req.url) == "https://api.anthropic.com/v1/messages"
    assert req.headers["x-api-key"] == "sk-ant-test-0000000"
    assert req.headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "Be brief." and body["messages"] == [{"role": "user", "content": "Hi"}]
    assert out.text == "Answer: 42" and out.input_tokens == 20


def test_gemini_request_and_parse_key_not_in_url(fake_llm):
    out = providers.complete(snap("gemini", "gemini-2.5-flash"), "Hi", "AIzaTEST0000000000000000000")
    req = fake_llm.requests[-1]
    body = json.loads(req.content)
    assert req.url.path == "/v1beta/models/gemini-2.5-flash:generateContent"
    assert "key=" not in str(req.url)
    assert req.headers["x-goog-api-key"] == "AIzaTEST0000000000000000000"
    assert body["systemInstruction"]["parts"][0]["text"] == "Be brief."
    assert body["generationConfig"] == {"temperature": 0.2, "maxOutputTokens": 64}
    assert out.output_tokens == 10


def test_paid_provider_without_key_fails_fast(fake_llm):
    with pytest.raises(providers.ProviderError, match="No API key"):
        providers.complete(snap("anthropic", "claude-x"), "Hi", None)
    assert fake_llm.requests == []


def test_retry_with_backoff_then_success(fake_llm):
    fake_llm.fail_next = [429, 503]
    sleeps = []
    out = providers.complete(snap("ollama", "qwen3:4b"), "Hi", None, sleep=sleeps.append)
    assert out.text == "Answer: 42" and len(fake_llm.requests) == 3 and len(sleeps) == 2


def test_non_retryable_error_is_clear_and_keyless(fake_llm):
    fake_llm.fail_next = [401]
    with pytest.raises(providers.ProviderError) as exc:
        providers.complete(snap("openai", "gpt-4o"), "Hi", "sk-secret-value-123456")
    assert "HTTP 401" in str(exc.value) and "sk-secret" not in str(exc.value)
    assert len(fake_llm.requests) == 1


def test_ollama_offline_message(fake_llm):
    fake_llm.offline = True
    with pytest.raises(providers.ProviderError, match="ollama serve"):
        providers.complete(snap("ollama", "qwen3:4b"), "Hi", None, sleep=lambda s: None)
    res = providers.test_connection(snap("ollama", "qwen3:4b"), None)
    assert res["ok"] is False and "ollama serve" in res["message"]


def test_test_connection_reports_missing_model(fake_llm):
    assert providers.test_connection(snap("ollama", "qwen3:4b"), None)["ok"]
    res = providers.test_connection(snap("ollama", "llama3:8b"), None)
    assert not res["ok"] and "not pulled" in res["message"]


def test_timeout_is_retryable_error():
    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)
    providers.set_transport(httpx.MockTransport(boom))
    with pytest.raises(providers.ProviderError, match="timed out"):
        providers.complete(snap("ollama", "qwen3:4b"), "Hi", None, sleep=lambda s: None)


def test_family_and_cost():
    assert providers.family("qwen3:4b", "ollama") == "qwen"
    assert providers.family("claude-sonnet-4-5", "anthropic") == "anthropic"
    assert providers.family("gpt-4o-mini", "openai_compatible") == "openai"
    assert providers.family("gemini-2.5-pro", "gemini") == "google"
    assert providers.cost_usd({"price_in": 3, "price_out": 15}, 1_000_000, 100_000) == pytest.approx(4.5)


@pytest.mark.parametrize("provider,model,payload", [
    ("ollama", "qwen3:4b", {"message": {"content": "cut"}, "done_reason": "length", "eval_count": 64}),
    ("openai", "gpt-4o", {"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}], "usage": {}}),
    ("anthropic", "claude-x", {"content": [{"type": "text", "text": "cut"}], "stop_reason": "max_tokens", "usage": {}}),
    ("gemini", "gemini-x", {"candidates": [{"content": {"parts": [{"text": "cut"}]}, "finishReason": "MAX_TOKENS"}]}),
])
def test_truncation_detected_for_every_provider(provider, model, payload):
    providers.set_transport(httpx.MockTransport(lambda req: httpx.Response(200, json=payload)))
    out = providers.complete(snap(provider, model), "Hi", "sk-test-SYNTHETIC-000000")
    assert out.text == "cut" and out.truncated is True
