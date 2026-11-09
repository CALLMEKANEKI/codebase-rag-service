import argparse
import json
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any

from eval.tools.corpus import (
    DEFAULT_PROFILES,
    ConfigurationError,
    CorpusProfile,
    enumerate_corpus,
    load_profiles,
    verify_checkout,
)
from eval.tools.symbols import build_symbols, count_lines, decode_source

CATEGORY_CODES = {
    "sym": "symbol_lookup",
    "und": "code_understanding",
    "xfile": "cross_file",
    "flow": "code_flow",
    "cfg": "configuration",
    "unans": "unanswerable",
}
CATEGORIES = set(CATEGORY_CODES.values())
ID_RE = re.compile(r"^([a-z]{2})-(sym|und|xfile|flow|cfg|unans)-\d{3}$")
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")
COMMON_SHORT_NAMES = {
    "get",
    "set",
    "run",
    "app",
    "add",
    "url",
    "use",
    "call",
    "send",
    "open",
    "load",
    "make",
    "path",
    "name",
    "type",
    "data",
    "file",
    "help",
    "init",
    "main",
    "test",
    "view",
    "read",
    "write",
    "create",
    "build",
    "parse",
    "check",
    "find",
    "new",
    "from",
    "to",
    "as",
    "in",
    "by",
    "id",
    "ok",
    "all",
    "any",
    "map",
    "list",
    "dict",
    "str",
    "int",
    "bool",
    "config",
    "client",
    "server",
    "request",
    "response",
}
TOP_REQUIRED = {
    "id",
    "schema_version",
    "repo",
    "commit_sha",
    "split",
    "category",
    "lexical_overlap",
    "question",
    "answerable",
    "relevant",
    "key_points",
    "unanswerable_type",
}
TOP_OPTIONAL = {"reference_answer", "notes"}
RELEVANT_KEYS = {
    "role",
    "file_path",
    "start_line",
    "end_line",
    "symbol",
    "order",
}
TARGETS = {"dev": 2, "test": 4}


def _normalize_question(question: str) -> str:
    chars = [
        char.lower()
        for char in question
        if not unicodedata.category(char).startswith("P")
    ]
    return " ".join("".join(chars).split())


def _field_error(errors: list[str], line: int, record_id: str, message: str) -> None:
    errors.append(f"{line}: {record_id}: {message}")


def _is_relative_posix_path(value: str) -> bool:
    path = PurePosixPath(value)
    return (
        bool(value)
        and "\\" not in value
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in value.split("/"))
    )


def _identifier_mentions(question: str, name: str, qualname: str) -> bool:
    tokens = IDENTIFIER_RE.findall(question)
    for token in tokens:
        components = token.split(".")
        matched = token == qualname or name in components
        if not matched:
            continue
        if len(name) >= 4 and name not in COMMON_SHORT_NAMES:
            return True
        quoted = re.search(rf"`{re.escape(name)}`", question) is not None
        function_call = re.search(rf"\b{re.escape(name)}\s*\(", question) is not None
        if quoted or function_call or "." in token:
            return True
    return False


def _validate_record(
    line: int,
    record: Any,
    profiles: dict[str, CorpusProfile],
    corpus_by_repo: dict[str, set[str]],
    lines_by_repo: dict[str, dict[str, int]],
    symbols_by_repo: dict[str, dict[str, list[dict[str, Any]]]],
    errors: list[str],
    questions: dict[str, tuple[int, str]],
    relevant_entries: list[tuple[int, dict[str, Any], list[dict[str, Any]]]],
) -> None:
    if not isinstance(record, dict):
        _field_error(errors, line, "?", "record must be an object")
        return
    raw_id = record.get("id")
    record_id = raw_id if isinstance(raw_id, str) else "?"
    for missing in sorted(TOP_REQUIRED - record.keys()):
        _field_error(errors, line, record_id, f"missing required field {missing}")
    for extra in sorted(record.keys() - TOP_REQUIRED - TOP_OPTIONAL):
        _field_error(errors, line, record_id, f"unknown field {extra}")

    if not isinstance(raw_id, str) or not ID_RE.fullmatch(raw_id):
        _field_error(
            errors,
            line,
            record_id,
            "id must match ^[a-z]{2}-(sym|und|xfile|flow|cfg|unans)-\\d{3}$",
        )
    schema_version = record.get("schema_version")
    if type(schema_version) is not int:
        _field_error(errors, line, record_id, "schema_version must be an integer")
    elif schema_version != 1:
        _field_error(errors, line, record_id, "schema_version must equal 1")

    repo = record.get("repo")
    profile = profiles.get(repo) if isinstance(repo, str) else None
    if not isinstance(repo, str):
        _field_error(errors, line, record_id, "repo must be a string")
    elif profile is None:
        _field_error(errors, line, record_id, f"repo is not in profiles: {repo}")

    commit_sha = record.get("commit_sha")
    if not isinstance(commit_sha, str):
        _field_error(errors, line, record_id, "commit_sha must be a string")
    elif profile and commit_sha != profile.commit_sha:
        _field_error(
            errors, line, record_id, "commit_sha does not match pinned profile SHA"
        )

    split = record.get("split")
    if not isinstance(split, str) or split not in {"dev", "test"}:
        _field_error(errors, line, record_id, "split must be dev or test")
    category = record.get("category")
    if not isinstance(category, str) or category not in CATEGORIES:
        _field_error(errors, line, record_id, "category is invalid")
    lexical = record.get("lexical_overlap")
    if not isinstance(lexical, str) or lexical not in {"high", "low"}:
        _field_error(errors, line, record_id, "lexical_overlap must be high or low")

    question = record.get("question")
    if not isinstance(question, str) or len(question.strip()) < 15:
        _field_error(
            errors,
            line,
            record_id,
            "question must be a string of at least 15 characters after stripping whitespace",
        )
    elif question.strip():
        normalized = _normalize_question(question)
        if normalized in questions:
            _field_error(
                errors,
                line,
                record_id,
                f"question duplicates line {questions[normalized][0]} after normalization",
            )
        else:
            questions[normalized] = (line, record_id)

    answerable = record.get("answerable")
    answerable_valid = type(answerable) is bool
    if not answerable_valid:
        _field_error(errors, line, record_id, "answerable must be a boolean")

    relevant = record.get("relevant")
    relevant_valid = isinstance(relevant, list)
    if not relevant_valid:
        _field_error(errors, line, record_id, "relevant must be an array")
        relevant = []
    key_points = record.get("key_points")
    points_valid = isinstance(key_points, list) and all(
        isinstance(point, str) and bool(point.strip()) for point in key_points
    )
    if not points_valid:
        _field_error(
            errors, line, record_id, "key_points must be an array of non-empty strings"
        )
        key_points = []
    unanswerable_type = record.get("unanswerable_type")
    if not (unanswerable_type is None or isinstance(unanswerable_type, str)):
        _field_error(
            errors, line, record_id, "unanswerable_type must be string or null"
        )
    for optional in TOP_OPTIONAL:
        if optional in record and not isinstance(record[optional], str):
            _field_error(errors, line, record_id, f"{optional} must be a string")

    groups = ID_RE.fullmatch(raw_id) if isinstance(raw_id, str) else None
    if groups and profile:
        if groups.group(1) != profile.id_prefix:
            _field_error(
                errors, line, record_id, "id prefix does not match repo profile"
            )
        expected_category = CATEGORY_CODES[groups.group(2)]
        if category != expected_category:
            _field_error(
                errors, line, record_id, "id group code does not match category"
            )

    parsed_relevant: list[dict[str, Any]] = []
    for index, item in enumerate(relevant):
        if not isinstance(item, dict):
            _field_error(
                errors, line, record_id, f"relevant[{index}] must be an object"
            )
            continue
        parsed_relevant.append(item)
        for extra in sorted(item.keys() - RELEVANT_KEYS):
            _field_error(
                errors, line, record_id, f"relevant[{index}] has unknown field {extra}"
            )
        for required in ("role", "file_path", "start_line", "end_line", "symbol"):
            if required not in item:
                _field_error(
                    errors, line, record_id, f"relevant[{index}] missing {required}"
                )
        role = item.get("role")
        if not isinstance(role, str) or role not in {"primary", "supporting"}:
            _field_error(errors, line, record_id, f"relevant[{index}].role is invalid")
        file_path = item.get("file_path")
        path_valid = isinstance(file_path, str) and _is_relative_posix_path(file_path)
        if not path_valid:
            _field_error(
                errors,
                line,
                record_id,
                f"relevant[{index}].file_path must be a non-empty relative POSIX path",
            )
        for integer_field in ("start_line", "end_line"):
            value = item.get(integer_field)
            if type(value) is not int:
                _field_error(
                    errors,
                    line,
                    record_id,
                    f"relevant[{index}].{integer_field} must be an integer",
                )
        if "order" in item and type(item["order"]) is not int:
            _field_error(
                errors, line, record_id, f"relevant[{index}].order must be an integer"
            )
        symbol = item.get("symbol")
        if not (symbol is None or isinstance(symbol, str)):
            _field_error(
                errors,
                line,
                record_id,
                f"relevant[{index}].symbol must be string or null",
            )

        if not path_valid:
            continue
        if (
            not isinstance(repo, str)
            or repo not in corpus_by_repo
            or file_path not in corpus_by_repo[repo]
        ):
            _field_error(
                errors,
                line,
                record_id,
                f"relevant[{index}].file_path is outside src_only corpus",
            )
            continue
        if (
            type(item.get("start_line")) is not int
            or type(item.get("end_line")) is not int
        ):
            continue
        start = item["start_line"]
        end = item["end_line"]
        file_lines = lines_by_repo[repo][file_path]
        if file_lines == 0:
            _field_error(
                errors,
                line,
                record_id,
                f"{file_path}: file rỗng (0 dòng), không thể dùng làm ground truth",
            )
            continue
        if start < 1:
            _field_error(
                errors, line, record_id, f"{file_path}: start_line must be >= 1"
            )
        if end < start:
            _field_error(
                errors, line, record_id, f"{file_path}: end_line must be >= start_line"
            )
        if end > file_lines:
            _field_error(
                errors,
                line,
                record_id,
                f"{file_path}: end_line exceeds {file_lines} lines",
            )
        if isinstance(symbol, str) and start >= 1 and end >= start:
            matching_symbols = [
                candidate
                for candidate in symbols_by_repo[repo][file_path]
                if symbol in {candidate["name"], candidate["qualname"]}
                and candidate["start_line"] <= end
                and start <= candidate["end_line"]
            ]
            if not matching_symbols:
                _field_error(
                    errors,
                    line,
                    record_id,
                    f"{file_path}: symbol {symbol!r} does not match an overlapping symbol",
                )
        relevant_entries.append((line, item, symbols_by_repo[repo][file_path]))

    seen_relevant: set[tuple[Any, ...]] = set()
    for item in parsed_relevant:
        file_path = item.get("file_path")
        start = item.get("start_line")
        end = item.get("end_line")
        role = item.get("role")
        if (
            isinstance(file_path, str)
            and type(start) is int
            and type(end) is int
            and isinstance(role, str)
        ):
            key = (file_path, start, end, role)
            if key in seen_relevant:
                _field_error(errors, line, record_id, "duplicate relevant item")
            seen_relevant.add(key)

    if answerable_valid and isinstance(category, str) and category in CATEGORIES:
        if (category == "unanswerable") != (answerable is False):
            _field_error(
                errors, line, record_id, "category and answerable are inconsistent"
            )
        if answerable:
            if unanswerable_type is not None:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "answerable records require unanswerable_type=null",
                )
            primary = [
                item for item in parsed_relevant if item.get("role") == "primary"
            ]
            if not primary:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "answerable records require a primary relevant item",
                )
            if points_valid and not 2 <= len(key_points) <= 4:
                _field_error(
                    errors, line, record_id, "answerable records require 2-4 key_points"
                )
        else:
            if not isinstance(unanswerable_type, str) or unanswerable_type not in {
                "not_in_repo",
                "false_premise",
            }:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "unanswerable_type must be not_in_repo or false_premise",
                )
            if relevant:
                _field_error(
                    errors, line, record_id, "unanswerable records require relevant=[]"
                )
            if key_points:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "unanswerable records require key_points=[]",
                )

    primary = [item for item in parsed_relevant if item.get("role") == "primary"]
    if category == "code_flow":
        orders = []
        for item in parsed_relevant:
            role = item.get("role")
            if role == "primary":
                order = item.get("order")
                if type(order) is not int or order < 1:
                    _field_error(
                        errors,
                        line,
                        record_id,
                        "code_flow primary order must be integer >= 1",
                    )
                else:
                    orders.append(order)
            elif "order" in item:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "supporting relevant items must not have order",
                )
        if len(orders) != len(set(orders)):
            _field_error(
                errors, line, record_id, "code_flow primary order values must be unique"
            )
        if sorted(orders) != list(range(1, len(orders) + 1)):
            _field_error(
                errors,
                line,
                record_id,
                "code_flow primary order must be continuous from 1",
            )
    else:
        for item in parsed_relevant:
            if "order" in item:
                _field_error(
                    errors,
                    line,
                    record_id,
                    "order is only allowed for code_flow primary items",
                )

    primary_count = len(primary)
    if answerable_valid and answerable and isinstance(category, str):
        valid_shape = True
        if category == "symbol_lookup":
            valid_shape = primary_count == 1
        elif category == "code_understanding":
            valid_shape = 1 <= primary_count <= 2
        elif category == "cross_file":
            primary_files = {
                item.get("file_path")
                for item in primary
                if isinstance(item.get("file_path"), str)
            }
            valid_shape = primary_count >= 2 and len(primary_files) >= 2
        elif category == "code_flow":
            primary_files = {
                item.get("file_path")
                for item in primary
                if isinstance(item.get("file_path"), str)
            }
            valid_shape = primary_count >= 3 and len(primary_files) >= 2
        elif category == "configuration":
            valid_shape = 1 <= primary_count <= 2
        if not valid_shape:
            _field_error(
                errors,
                line,
                record_id,
                f"invalid relevant shape for category {category}",
            )


def validate_records(
    records: list[tuple[int, Any]],
    profiles: dict[str, CorpusProfile],
    corpus_by_repo: dict[str, set[str]],
    lines_by_repo: dict[str, dict[str, int]],
    symbols_by_repo: dict[str, dict[str, list[dict[str, Any]]]],
    *,
    phase: str,
    require_complete: bool,
) -> tuple[list[str], list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    questions: dict[str, tuple[int, str]] = {}
    seen_ids: dict[str, int] = {}
    relevant_entries: list[tuple[int, dict[str, Any], list[dict[str, Any]]]] = []
    counts: Counter[tuple[str, str, str]] = Counter()
    unanswerable_counts: Counter[tuple[str, str, str]] = Counter()

    for line, record in records:
        record_id = record.get("id", "?") if isinstance(record, dict) else "?"
        if isinstance(record_id, str):
            if record_id in seen_ids:
                _field_error(
                    errors,
                    line,
                    record_id,
                    f"duplicate id (line {seen_ids[record_id]})",
                )
            else:
                seen_ids[record_id] = line
        _validate_record(
            line,
            record,
            profiles,
            corpus_by_repo,
            lines_by_repo,
            symbols_by_repo,
            errors,
            questions,
            relevant_entries,
        )
        if not isinstance(record, dict):
            continue
        repo, category, split = (
            record.get("repo"),
            record.get("category"),
            record.get("split"),
        )
        if (
            isinstance(repo, str)
            and repo in profiles
            and isinstance(category, str)
            and category in CATEGORIES
            and isinstance(split, str)
            and split in TARGETS
        ):
            counts[(repo, category, split)] += 1
            if (
                record.get("category") == "unanswerable"
                and record.get("answerable") is False
            ):
                kind = record.get("unanswerable_type")
                if isinstance(kind, str) and kind in {"not_in_repo", "false_premise"}:
                    unanswerable_counts[(repo, split, kind)] += 1

        for item in (
            record.get("relevant", [])
            if isinstance(record.get("relevant"), list)
            else []
        ):
            if isinstance(item, dict):
                start = item.get("start_line")
                end = item.get("end_line")
                if type(start) is int and type(end) is int and end - start + 1 > 150:
                    warnings.append(
                        f"{record_id}: relevant range {item.get('file_path')}:{start}-{end} exceeds 150 lines"
                    )

    selected_splits = {"dev", "test"} if phase == "all" else {phase}
    for repo in profiles:
        for category in sorted(CATEGORIES):
            for split in ("dev", "test"):
                if split not in selected_splits:
                    continue
                count = counts[(repo, category, split)]
                target = TARGETS[split]
                print(f"{repo} | {category} | {split}: {count}/{target}")
                if require_complete and count != target:
                    errors.append(
                        f"0: ?: {repo} {category} {split} completeness is {count}, expected {target}"
                    )

    for repo, split in sorted(
        {(repo, split) for repo, split, _kind in unanswerable_counts}
    ):
        not_in_repo = unanswerable_counts[(repo, split, "not_in_repo")]
        false_premise = unanswerable_counts[(repo, split, "false_premise")]
        if abs(not_in_repo - false_premise) > 1:
            warnings.append(
                f"{repo} {split}: unanswerable_type imbalance "
                f"not_in_repo={not_in_repo}, false_premise={false_premise}"
            )

    for line, record in records:
        if not isinstance(record, dict):
            continue
        repo = record.get("repo")
        split = record.get("split")
        primary_symbols: list[tuple[str, str]] = []
        for item in (
            record.get("relevant", [])
            if isinstance(record.get("relevant"), list)
            else []
        ):
            if not isinstance(item, dict) or item.get("role") != "primary":
                continue
            file_path = item.get("file_path")
            symbol = item.get("symbol")
            if (
                not isinstance(repo, str)
                or not isinstance(file_path, str)
                or not isinstance(symbol, str)
            ):
                continue
            for candidate in symbols_by_repo.get(repo, {}).get(file_path, []):
                if symbol in {candidate["name"], candidate["qualname"]}:
                    identifier = (candidate["name"], candidate["qualname"])
                    if identifier not in primary_symbols:
                        primary_symbols.append(identifier)
        if not primary_symbols or record.get("answerable") is not True:
            continue
        question = record.get("question")
        lexical = record.get("lexical_overlap")
        if not isinstance(question, str) or not isinstance(lexical, str):
            continue
        if lexical not in ("high", "low"):
            continue
        mention = any(
            _identifier_mentions(question, name, qualname)
            for name, qualname in primary_symbols
        )
        record_id = record.get("id", "?")
        if lexical == "low" and mention:
            warnings.append(
                f"{record_id}: lexical_overlap=low but a primary identifier appears"
            )
        elif lexical == "high" and not mention:
            warnings.append(
                f"{record_id}: lexical_overlap=high but no primary identifier appears"
            )

    for repo in profiles:
        for split in ("dev", "test"):
            split_records = [
                record
                for _, record in records
                if isinstance(record, dict)
                and record.get("repo") == repo
                and record.get("split") == split
            ]
            low_count = sum(
                item.get("lexical_overlap") == "low" for item in split_records
            )
            if len(split_records) >= 10:
                ratio = low_count / len(split_records)
                if not 0.4 <= ratio <= 0.6:
                    warnings.append(
                        f"{repo} {split}: lexical_overlap=low ratio {ratio:.1%} is outside 40-60%"
                    )
    return errors, warnings, warnings


def _parse_dataset(path: Path) -> tuple[list[tuple[int, Any]], list[str]]:
    records: list[tuple[int, Any]] = []
    parse_errors: list[str] = []
    try:
        with path.open(encoding="utf-8") as dataset:
            for line_number, raw in enumerate(dataset, 1):
                if not raw.strip():
                    continue
                try:
                    records.append((line_number, json.loads(raw)))
                except json.JSONDecodeError as exc:
                    parse_errors.append(f"{line_number}: ?: invalid JSON: {exc}")
    except OSError as exc:
        raise ConfigurationError(f"cannot read dataset {path}: {exc}") from exc
    return records, parse_errors


def validate_dataset_file(
    dataset_path: Path,
    repo_roots: dict[str, Path],
    profile_path: Path,
    *,
    phase: str,
    skip_sha_check: bool,
    require_complete: bool,
) -> int:
    profiles = load_profiles(profile_path)
    for repo_id in repo_roots:
        if repo_id not in profiles:
            raise ConfigurationError(f"repo-root supplied for unknown repo: {repo_id}")

    records, parse_errors = _parse_dataset(dataset_path)
    data_repo_ids = {
        record.get("repo")
        for _, record in records
        if isinstance(record, dict) and isinstance(record.get("repo"), str)
    }
    configured_repos = data_repo_ids & profiles.keys()
    for repo_id in sorted(configured_repos):
        if repo_id not in repo_roots:
            raise ConfigurationError(
                f"missing --repo-root for repo in dataset: {repo_id}"
            )
        root = repo_roots[repo_id]
        if not root.is_dir():
            raise ConfigurationError(
                f"repo_root is not a directory for {repo_id}: {root}"
            )
        if not skip_sha_check:
            print(
                verify_checkout(root, profiles[repo_id].commit_sha, profiles[repo_id])
            )

    if parse_errors:
        for error in parse_errors:
            print(error)
        return 1

    corpus_by_repo: dict[str, set[str]] = {}
    lines_by_repo: dict[str, dict[str, int]] = {}
    symbols_by_repo: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for repo_id in sorted(configured_repos):
        root = repo_roots[repo_id]
        file_paths = enumerate_corpus(root, profiles[repo_id])
        if not file_paths:
            raise ConfigurationError(
                f"src_only corpus contains no Python files for {repo_id}"
            )
        corpus_by_repo[repo_id] = set(file_paths)
        _records_for_repo, records_by_file, _empty, source_errors = build_symbols(
            root, repo_id, file_paths
        )
        if source_errors:
            for error in source_errors:
                print(f"Configuration error: {error}", file=sys.stderr)
            raise ConfigurationError(f"cannot parse corpus source for {repo_id}")
        symbols_by_repo[repo_id] = records_by_file
        lines_by_repo[repo_id] = {}
        for file_path in file_paths:
            source = (root / file_path).read_bytes()
            lines_by_repo[repo_id][file_path] = count_lines(
                decode_source(source, file_path)
            )

    errors, warnings, _ = validate_records(
        records,
        profiles,
        corpus_by_repo,
        lines_by_repo,
        symbols_by_repo,
        phase=phase,
        require_complete=require_complete,
    )
    errors.extend(parse_errors)
    for error in errors:
        print(error)
    for warning in warnings:
        print(f"WARNING: {warning}")
    return 1 if errors else 0


def _parse_repo_root(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("--repo-root must be <repo_id>=<dir>")
    repo_id, path = value.split("=", 1)
    if not repo_id or not path:
        raise argparse.ArgumentTypeError("--repo-root must be <repo_id>=<dir>")
    return repo_id, Path(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument(
        "--repo-root", required=True, action="append", type=_parse_repo_root
    )
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--phase", choices=("dev", "test", "all"), default="all")
    parser.add_argument("--skip-sha-check", action="store_true")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args(argv)
    repo_roots = dict(args.repo_root)
    try:
        return validate_dataset_file(
            args.dataset,
            repo_roots,
            args.profiles,
            phase=args.phase,
            skip_sha_check=args.skip_sha_check,
            require_complete=args.require_complete,
        )
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
