from dataclasses import dataclass

from eval.metrics import hit_at_k, ndcg_at_k, recall_at_k, reciprocal_rank


@dataclass
class Result:
    chunk: dict


def test_retrieval_metrics_use_overlapping_line_ranges():
    ranked = [
        Result({"file_path": "a.py", "start_line": 1, "end_line": 4}),
        Result({"file_path": "b.py", "start_line": 10, "end_line": 20}),
    ]
    relevant = [
        {"file_path": "b.py", "start_line": 15, "end_line": 17},
        {"file_path": "c.py", "start_line": 1, "end_line": 2},
    ]
    assert hit_at_k(ranked, relevant, 2) == 1.0
    assert recall_at_k(ranked, relevant, 2) == 0.5
    assert reciprocal_rank(ranked, relevant) == 0.5
    assert 0 < ndcg_at_k(ranked, relevant, 2) < 1
