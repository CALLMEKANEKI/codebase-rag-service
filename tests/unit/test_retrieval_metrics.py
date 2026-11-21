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



def test_ndcg_does_not_count_duplicate_chunks_for_the_same_ground_truth():
    ranked = [
        Result({"file_path": "a.py", "start_line": 10, "end_line": 20}),
        Result({"file_path": "a.py", "start_line": 12, "end_line": 18}),
    ]
    relevant = [{"file_path": "a.py", "start_line": 14, "end_line": 16}]

    assert ndcg_at_k(ranked, relevant, 2) == 1.0


def test_ndcg_is_bounded_when_one_chunk_overlaps_multiple_ground_truth_ranges():
    ranked = [
        Result({"file_path": "a.py", "start_line": 1, "end_line": 30}),
        Result({"file_path": "a.py", "start_line": 10, "end_line": 20}),
    ]
    relevant = [
        {"file_path": "a.py", "start_line": 3, "end_line": 5},
        {"file_path": "a.py", "start_line": 15, "end_line": 17},
    ]

    assert 0.0 <= ndcg_at_k(ranked, relevant, 2) <= 1.0
