from __future__ import annotations

import re
from typing import Any

from rag.generation.context import ContextSource

_MARKER = re.compile(r"\[(\d+)\]")
_NO_EVIDENCE_TOKEN = "[[NO_EVIDENCE]]"
_ENGLISH_REFUSAL = re.compile(
    r"(?:"
    r"\b(?:i\s+(?:cannot|can't|can not|am unable to)\s+"
    r"(?:answer|help|provide|determine|write))\b"
    r"|\b(?:not enough|insufficient)\s+(?:evidence|information|context)\b"
    r"|\b(?:i\s+(?:do not|don't)\s+have\s+enough\s+"
    r"(?:evidence|information|context))\b"
    r"|\b(?:i can only answer|outside the scope of this repository)\b"
    r")",
    re.IGNORECASE,
)
_VIETNAMESE_REFUSAL = re.compile(
    r"(?:"
    r"không đủ\s+(?:bằng chứng|thông tin|dữ liệu)"
    r"|không tìm thấy\s+(?:đủ\s+)?(?:bằng chứng|thông tin|dữ liệu)"
    r"|không thể\s+(?:trả lời|xác định|cung cấp)"
    r"|nằm ngoài\s+phạm vi"
    r"|chỉ có thể trả lời\s+(?:các câu hỏi|những câu hỏi)"
    r")",
    re.IGNORECASE,
)
_VIETNAMESE_CHARACTERS = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừữựýỳỷỹỵ]", re.IGNORECASE)

_NO_EVIDENCE_EN = (
    "I could not find enough evidence in the indexed repository to answer that question."
)
_NO_EVIDENCE_VI = (
    "Tôi không tìm thấy đủ bằng chứng trong mã nguồn đã lập chỉ mục để trả lời câu hỏi này."
)


def no_evidence_answer(question: str) -> str:
    """Return a stable refusal in the language most clearly indicated by the query."""
    if _VIETNAMESE_CHARACTERS.search(question):
        return _NO_EVIDENCE_VI
    return _NO_EVIDENCE_EN


def _is_refusal(answer: str) -> bool:
    normalized = answer.strip()
    return (
        _NO_EVIDENCE_TOKEN.casefold() in normalized.casefold()
        or bool(_ENGLISH_REFUSAL.search(normalized))
        or bool(_VIETNAMESE_REFUSAL.search(normalized))
    )


def validate_citations(
    answer: str,
    sources: tuple[ContextSource, ...],
    *,
    question: str = "",
) -> tuple[str, list[dict[str, Any]]]:
    """Keep only citations that map to supplied context; refusals never cite sources."""
    if _is_refusal(answer):
        return no_evidence_answer(question), []

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
        return no_evidence_answer(question), []

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
