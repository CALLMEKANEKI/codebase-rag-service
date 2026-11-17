from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class GenerationResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class LanguageModel(Protocol):
    def generate(self, *, question: str, context: str) -> GenerationResult: ...
