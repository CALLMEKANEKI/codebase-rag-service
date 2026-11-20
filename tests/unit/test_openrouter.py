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


def test_openrouter_tries_next_model_after_timeout(monkeypatch):
    requests = []

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            if len(requests) == 1:
                raise httpx.TimeoutException("first model timed out")
            return FakeResponse(
                {
                    "model": "second/model:free",
                    "choices": [{"message": {"content": "Fallback answer [1]."}}],
                    "usage": {"prompt_tokens": 19, "completion_tokens": 7},
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

    assert [request["model"] for request in requests] == [
        "first/model:free",
        "second/model:free",
    ]
    assert all("models" not in request for request in requests)
    assert result.text == "Fallback answer [1]."
    assert result.input_tokens == 19
    assert result.output_tokens == 7


def test_openrouter_reraises_last_timeout_after_all_models_timeout(monkeypatch):
    requests = []
    timeouts = []

    class TimeoutClient:
        def __init__(self, **kwargs):
            pass

        def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            timeout = httpx.TimeoutException(f"{kwargs['json']['model']} timed out")
            timeouts.append(timeout)
            raise timeout

        def close(self):
            pass

    monkeypatch.setattr(module.httpx, "Client", TimeoutClient)
    llm = OpenRouterLanguageModel(
        api_key="test-key",
        models=["first/model:free", "second/model:free"],
    )

    with pytest.raises(httpx.TimeoutException) as exc_info:
        llm.generate(question="question", context="[1] source")

    assert [request["model"] for request in requests] == [
        "first/model:free",
        "second/model:free",
    ]
    assert all("models" not in request for request in requests)
    assert exc_info.value is timeouts[-1]


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
            requests.append(kwargs["json"])
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

    assert len(requests) == 1
    assert requests[0]["model"] == "first/model:free"
    assert "models" not in requests[0]
    assert result.text == "Answer [1]."
    assert result.input_tokens == 10
    assert result.output_tokens == 3
