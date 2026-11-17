from __future__ import annotations

import re
from typing import Any

from rag.generation.context import ContextSource

_MARKER = re.compile(r"\[(\d+)\]")


def validate_citations(
    answer: str, sources: tuple[ContextSource, ...]
) -> tuple[str, list[dict[str, Any]]]:
    """Keep only citations that map to context actually supplied to the model."""
    by_id = {source.ref_id: source for source in sources}
    cited_ids: list[int] = []
    invalid_found = False

    def replace(match: re.Match[str]) -> str:
        nonlocal invalid_found
        ref_id = int(match.group(1))
        if ref_id not in by_id:
            invalid_found = True
            return ""
        if ref_id not in cited_ids:
            cited_ids.append(ref_id)
        return match.group(0)

    clean_answer = _MARKER.sub(replace, answer).strip()
    if invalid_found:
        clean_answer = re.sub(r"[ \t]{2,}", " ", clean_answer)
    if not cited_ids:
        return (
            (
                "I could not find enough evidence in the indexed repository to answer "
                " "that question."
            ),
            [],
        )

    citations = []
    for ref_id in cited_ids:
        source = by_id[ref_id]
        chunk = source.chunk
        citations.append(
            {
                "ref_id": ref_id,
                "file_path": str(chunk.get("file_path") or ""),
                "symbol": chunk.get("qualname") or chunk.get("symbol_name"),
                "symbol_kind": chunk.get("symbol_kind"),
                "start_line": int(chunk.get("start_line") or 1),
                "end_line": int(chunk.get("end_line") or chunk.get("start_line") or 1),
                "snippet": str(chunk.get("text") or ""),
                "score": source.score,
            }
        )
    return clean_answer, citations
