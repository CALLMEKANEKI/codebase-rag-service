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


def test_openrouter_tries_models_in_order_until_one_succeeds(monkeypatch):
    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            requests.append(kwargs["json"]["model"])
            if len(requests) == 1:
                request = httpx.Request("POST", url)
                response = httpx.Response(404, request=request)
                raise httpx.HTTPStatusError(
                    "model not found", request=request, response=response
                )
            return FakeResponse(
                {
                    "model": "second/model:free",
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
        models=["provider/model-khong-ton-tai:free", "second/model:free"],
    )
    result = llm.generate(question="What is FastAPI?", context="[1] source")

    assert requests == [
        "provider/model-khong-ton-tai:free",
        "second/model:free",
    ]
    assert result.text == "FastAPI is the app class [1]."
    assert result.input_tokens == 22
    assert result.output_tokens == 8


def test_openrouter_raises_after_all_models_fail(monkeypatch):
    requests = []

    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            requests.append(kwargs["json"]["model"])
            request = httpx.Request("POST", url)
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError(
                "model not found", request=request, response=response
            )

        def close(self):
            pass

    monkeypatch.setattr(module.httpx, "Client", FailingClient)
    llm = OpenRouterLanguageModel(
        api_key="test-key",
        models=["first/model:free", "second/model:free"],
    )

    with pytest.raises(module.LLMError):
        llm.generate(question="question", context="[1] source")

    assert requests == ["first/model:free", "second/model:free"]


def test_openrouter_uses_first_model_when_it_succeeds(monkeypatch):
    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            requests.append(kwargs["json"]["model"])
            return FakeResponse(
                {
                    "model": "first/model:free",
                    "choices": [{"message": {"content": "Answer [1]."}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3},
                }
            )

        def close(self):
            pass

    monkeypatch.setattr(module.httpx, "Client", FakeClient)
    llm = OpenRouterLanguageModel(
        api_key="test-key",
        models=["first/model:free", "second/model:free"],
    )
    result = llm.generate(question="question", context="[1] source")

    assert requests == ["first/model:free"]
    assert result.text == "Answer [1]."
