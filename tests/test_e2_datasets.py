import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS = ROOT / "eval" / "datasets"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_e2_dataset_counts_ids_and_splits_are_consistent():
    dev = read_jsonl(DATASETS / "dev" / "E2_Dev_24.jsonl")
    dev_en = read_jsonl(DATASETS / "dev" / "E2_Dev_24_en.jsonl")
    test = read_jsonl(DATASETS / "test" / "E2_Test_48.jsonl")

    assert len(dev) == 24
    assert len(dev_en) == 24
    assert len(test) == 48
    assert all(row["split"] == "dev" for row in dev + dev_en)
    assert all(row["split"] == "test" for row in test)
    assert len({row["id"] for row in dev}) == 24
    assert len({row["id"] for row in dev_en}) == 24
    assert len({row["id"] for row in test}) == 48
    assert {row["id"] for row in dev} == {row["id"] for row in dev_en}


def test_english_dev_is_a_question_only_translation_of_vietnamese_dev():
    dev = read_jsonl(DATASETS / "dev" / "E2_Dev_24.jsonl")
    dev_en = read_jsonl(DATASETS / "dev" / "E2_Dev_24_en.jsonl")
    translated = {row["id"]: row for row in dev_en}

    for original in dev:
        english = translated[original["id"]]
        assert english["question"] != original["question"]
        assert all(
            english[key] == value
            for key, value in original.items()
            if key != "question"
        )
        assert english["question"].isascii()


def test_all_unanswerable_records_have_explanatory_reference_answers():
    dev = read_jsonl(DATASETS / "dev" / "E2_Dev_24.jsonl")
    test = read_jsonl(DATASETS / "test" / "E2_Test_48.jsonl")

    for row in dev + test:
        assert row["question"].strip()
        if row["answerable"]:
            assert row["relevant"]
            assert 2 <= len(row["key_points"]) <= 4
        else:
            assert row["relevant"] == []
            assert row["key_points"] == []
            assert row["unanswerable_type"] in {"not_in_repo", "false_premise"}
            assert row["reference_answer"].strip()


def test_test_unanswerables_are_not_yes_no_questions():
    test = read_jsonl(DATASETS / "test" / "E2_Test_48.jsonl")
    unanswerable = [row for row in test if not row["answerable"]]

    assert len(unanswerable) == 8
    assert all(
        not row["question"].lstrip().lower().startswith(("does ", "is ", "are ", "do "))
        for row in unanswerable
    )


def test_reviewed_test_ground_truth_ranges_and_lexical_labels_are_updated():
    test = {
        row["id"]: row for row in read_jsonl(DATASETS / "test" / "E2_Test_48.jsonl")
    }

    encoder = test["fa-und-003"]["relevant"][0]
    assert (encoder["start_line"], encoder["end_line"]) == (243, 258)

    flow = test["fa-flow-006"]["relevant"]
    assert any(
        item["file_path"] == "fastapi/encoders.py"
        and item["start_line"] == 243
        and item["end_line"] == 258
        for item in flow
    )
    assert test["fa-cfg-005"]["lexical_overlap"] == "low"
    assert test["fl-sym-003"]["lexical_overlap"] == "low"
    assert test["fl-und-003"]["lexical_overlap"] == "low"
