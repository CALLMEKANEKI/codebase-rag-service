import argparse
import json
import sys
from pathlib import Path

from eval.tools.corpus import (
    DEFAULT_PROFILES,
    ConfigurationError,
    enumerate_corpus,
    load_profiles,
    verify_checkout,
)
from eval.tools.symbols import build_symbols


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--repo-root", required=True, type=Path)
    parser.add_argument("--profiles", type=Path, default=DEFAULT_PROFILES)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--outline", type=Path)
    parser.add_argument("--skip-sha-check", action="store_true")
    args = parser.parse_args(argv)

    try:
        profiles = load_profiles(args.profiles)
        if args.repo not in profiles:
            raise ConfigurationError(f"unknown repo in profiles: {args.repo}")
        profile = profiles[args.repo]
        if args.skip_sha_check:
            print("Snapshot check skipped")
        else:
            print(verify_checkout(args.repo_root, profile.commit_sha, profile))
        file_paths = enumerate_corpus(args.repo_root, profile)
        records, records_by_file, empty_files, errors = build_symbols(
            args.repo_root, args.repo, file_paths
        )
        if errors:
            print("Python source errors:", file=sys.stderr)
            for error in errors:
                print(f"  {error}", file=sys.stderr)
            return 1

        slug = args.repo.rsplit("/", 1)[-1]
        out_path = args.out or Path(f"eval/work/symbols_{slug}.jsonl")
        outline_path = args.outline or Path(f"eval/work/outline_{slug}.txt")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        outline_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8", newline="\n") as output:
            for record in records:
                output.write(json.dumps(record, ensure_ascii=False) + "\n")
        with outline_path.open("w", encoding="utf-8", newline="\n") as output:
            for file_path in file_paths:
                file_records = records_by_file[file_path]
                if not file_records:
                    output.write(f"{file_path} (empty file, 0 lines, no symbols)\n")
                    continue
                output.write(f"{file_path}\n")
                for record in file_records:
                    doc = (
                        f" | {record['doc_first_line']}"
                        if record["doc_first_line"]
                        else ""
                    )
                    output.write(
                        f"{record['kind']} {record['qualname']} "
                        f"L{record['start_line']}-{record['end_line']}{doc}\n"
                    )

        print(f"Corpus files: {len(file_paths)}")
        print(f"Empty files: {len(empty_files)}")
        for file_path in empty_files:
            print(f"  {file_path}")
        print(f"Symbols: {len(records)}")
        for file_path in file_paths:
            print(f"  {file_path}: {len(records_by_file[file_path])}")
        print(f"JSONL: {out_path}")
        print(f"Outline: {outline_path}")
        return 0
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
