from __future__ import annotations

import httpx
import pytest

import rag.generation.openrouter as module
from rag.generation.openrouter import OpenRouterLanguageModel


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_openrouter_sends_ordered_fallbacks_and_returns_usage(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        def post(self, url, **kwargs):
            captured["url"] = url
            captured["request"] = kwargs
            return FakeResponse(
                {
                    "model": "provider/actual-model:free",
                    "choices": [
                        {"message": {"content": "FastAPI is the app class [1]."}}
                    ],
                    "usage": {"prompt_tokens": 22, "completion_tokens": 8},
                }
            )

        def close(self):
            pass

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    llm = OpenRouterLanguageModel(
        api_key="test-key",
        models=["first/model:free", "second/model:free"],
        timeout_ms=15_000,
        site_url="https://example.test",
    )
    result = llm.generate(question="What is FastAPI?", context="[1] source")

    assert result.text == "FastAPI is the app class [1]."
    assert result.input_tokens == 22
    assert result.output_tokens == 8
    assert captured["request"]["json"]["models"] == [
        "first/model:free",
        "second/model:free",
    ]
    assert captured["request"]["json"]["stream"] is False
    assert captured["client"]["headers"]["Authorization"] == "Bearer test-key"
    assert captured["client"]["headers"]["HTTP-Referer"] == "https://example.test"


def test_openrouter_rejects_non_free_models():
    with pytest.raises(ValueError, match="Only OpenRouter free models"):
        OpenRouterLanguageModel(
            api_key="test-key",
            models=["openai/some-paid-model"],
        )


def test_openrouter_wraps_provider_errors(monkeypatch):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            response = httpx.Response(429, request=request)
            raise httpx.HTTPStatusError(
                "rate limited", request=request, response=response
            )

        def close(self):
            pass

    monkeypatch.setattr(module.httpx, "Client", FailingClient)
    llm = OpenRouterLanguageModel(api_key="test-key", models=["model/name:free"])
    with pytest.raises(module.LLMError):
        llm.generate(question="question", context="[1] source")
