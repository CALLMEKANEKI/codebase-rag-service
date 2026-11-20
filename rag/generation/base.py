from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """Raised when the configured language-model provider fails."""


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LanguageModel(Protocol):
    def generate(self, *, question: str, context: str) -> GenerationResult: ...
