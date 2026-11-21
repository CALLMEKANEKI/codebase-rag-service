from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import Any


def is_relevant(chunk: dict[str, Any], relevant: Iterable[dict[str, Any]]) -> bool:
    path = str(chunk.get("file_path") or "")
    start = int(chunk.get("start_line") or 1)
    end = int(chunk.get("end_line") or start)
    for item in relevant:
        if path != item.get("file_path"):
            continue
        gold_start = int(item.get("start_line") or 1)
        gold_end = int(item.get("end_line") or gold_start)
        if start <= gold_end and gold_start <= end:
            return True
    return False


def hit_at_k(
    ranked: Sequence[Any], relevant: Iterable[dict[str, Any]], k: int
) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    gold = list(relevant)
    return float(any(is_relevant(item.chunk, gold) for item in ranked[:k]))


def recall_at_k(
    ranked: Sequence[Any], relevant: Iterable[dict[str, Any]], k: int
) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    gold = list(relevant)
    if not gold:
        return 0.0
    found = {
        (item.get("file_path"), item.get("start_line"), item.get("end_line"))
        for item in gold
        if any(is_relevant(result.chunk, [item]) for result in ranked[:k])
    }
    return len(found) / len(gold)


def reciprocal_rank(ranked: Sequence[Any], relevant: Iterable[dict[str, Any]]) -> float:
    gold = list(relevant)
    for rank, result in enumerate(ranked, start=1):
        if is_relevant(result.chunk, gold):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(
    ranked: Sequence[Any], relevant: Iterable[dict[str, Any]], k: int
) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    gold = list(relevant)
    if not gold:
        return 0.0
    unique_gold = {
        (item.get("file_path"), item.get("start_line"), item.get("end_line"))
        for item in gold
    }
    matched: set[tuple[Any, Any, Any]] = set()
    gains: list[float] = []
    for result in ranked[:k]:
        newly_matched = {
            key
            for key in unique_gold - matched
            if is_relevant(
                result.chunk,
                [
                    {
                        "file_path": key[0],
                        "start_line": key[1],
                        "end_line": key[2],
                    }
                ],
            )
        }
        gains.append(1.0 if newly_matched else 0.0)
        matched.update(newly_matched)
    dcg = sum(gain / math.log2(rank + 1) for rank, gain in enumerate(gains, start=1))
    ideal_hits = min(len(unique_gold), k)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / ideal if ideal else 0.0
