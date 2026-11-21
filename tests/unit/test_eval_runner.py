from __future__ import annotations

import json

import pytest

from eval.runner import _aggregate, _load_dev_dataset, _percentile


def test_percentile_interpolates_between_observations():
    assert _percentile([10.0, 20.0, 30.0], 0.5) == 20.0
    assert _percentile([10.0, 20.0], 0.95) == pytest.approx(19.5)
    assert _percentile([], 0.95) is None


def test_aggregate_reports_metrics_for_answerable_questions_only():
    rows = [
        {
            "repo": "pallets/flask",
            "category": "symbol_lookup",
            "answerable": True,
            "metrics": {
                "hit_at_5": 1.0,
                "recall_at_5": 0.5,
                "recall_at_10": 1.0,
                "mrr_at_10": 1.0,
                "ndcg_at_10": 1.0,
            },
            "retrieval_ms": 10.0,
            "reranker_ms": None,
            "total_ms": 10.0,
        },
        {
            "repo": "pallets/flask",
            "category": "unanswerable",
            "answerable": False,
            "metrics": {
                "hit_at_5": 0.0,
                "recall_at_5": 0.0,
                "recall_at_10": 0.0,
                "mrr_at_10": 0.0,
                "ndcg_at_10": 0.0,
            },
            "retrieval_ms": 20.0,
            "reranker_ms": None,
            "total_ms": 20.0,
        },
    ]

    result = _aggregate(rows)
    overall = result["overall"]
    assert overall["questions"] == 2
    assert overall["answerable_questions"] == 1
    assert overall["unanswerable_questions"] == 1
    assert overall["hit_at_5"] == 1.0
    assert overall["recall_at_5"] == 0.5
    assert overall["retrieval_ms_p50"] == 15.0
    assert result["by_repo"]["pallets/flask"]["questions"] == 2
    assert result["by_category"]["unanswerable"]["hit_at_5"] is None


def test_dev_loader_rejects_a_dataset_outside_dev_split(tmp_path):
    dataset = tmp_path / "E2_Test_48.jsonl"
    dataset.write_text(
        json.dumps({"id": "fl-sym-001", "split": "test"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="only accepts dataset files"):
        _load_dev_dataset(dataset)
