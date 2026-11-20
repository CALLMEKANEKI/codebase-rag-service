from types import SimpleNamespace

import rag.generation.gemini as gemini_module
from rag.generation.gemini import GeminiLanguageModel


def test_gemini_adapter_sends_bounded_grounded_prompt_and_returns_usage(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                text="The handler dispatches requests [1].",
                usage_metadata=SimpleNamespace(
                    prompt_token_count=15,
                    candidates_token_count=7,
                ),
            )

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client_options"] = kwargs
            self.models = FakeModels()
            self.closed = False

        def close(self):
            self.closed = True

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    model = GeminiLanguageModel(
        api_key="unit-test-key",
        model="gemini-test",
        timeout_ms=12_000,
    )

    result = model.generate(
        question="What does dispatch_request do?",
        context="[1] src/app.py:10-20 (dispatch_request)\ndef dispatch_request(): pass",
    )

    assert result.text.endswith("[1].")
    assert result.input_tokens == 15
    assert result.output_tokens == 7
    assert captured["model"] == "gemini-test"
    assert "untrusted source-code data" in captured["contents"]
    assert "dispatch_request" in captured["contents"]
    assert captured["config"].temperature == 0.1
    assert captured["config"].max_output_tokens == 1200
    assert captured["client_options"]["http_options"].timeout == 12_000
    model.close()


def test_gemini_adapter_wraps_provider_errors(monkeypatch):
    class FailingModels:
        def generate_content(self, **kwargs):
            raise RuntimeError("provider failure")

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FailingModels()

        def close(self):
            pass

    monkeypatch.setattr(gemini_module.genai, "Client", FakeClient)
    model = GeminiLanguageModel(api_key="unit-test-key")

    try:
        model.generate(question="question", context="[1] source")
    except gemini_module.LLMError as exc:
        assert str(exc) == "Gemini generation failed"
    else:
        raise AssertionError("provider errors should be wrapped as LLMError")
