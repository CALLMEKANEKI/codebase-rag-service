# Evaluation dataset schema v1

This document summarizes the v1 dataset contract and the corpus, snapshot, and
validation rules used by the local evaluation tools.

## Record shape

Every JSON object contains all these required top-level fields:

| Field | Type and allowed values |
|---|---|
| `id` | Unique string matching `^[a-z]{2}-(sym\|und\|xfile\|flow\|cfg\|unans)-\d{3}$`. Its two-letter prefix is assigned to the repository in `eval/corpus_profiles.yaml`; the group code must match `category`. |
| `schema_version` | Integer `1`; booleans, floats, and numeric strings are not integers for this field. |
| `repo` | Repository key present in the corpus profiles. |
| `commit_sha` | The exact pinned 40-character lowercase commit SHA for that repository. |
| `split` | `dev` or `test`. |
| `category` | `symbol_lookup`, `code_understanding`, `cross_file`, `code_flow`, `configuration`, or `unanswerable`. |
| `lexical_overlap` | `high` or `low`, including on unanswerable records. |
| `question` | String of at least 15 characters; normalized questions must be unique. |
| `answerable` | Boolean. `unanswerable` category and `false` answerability must agree in both directions. |
| `relevant` | Array of ground-truth locations; required even when empty. |
| `key_points` | Array of non-empty strings; answerable records have 2–4, unanswerable records have none. |
| `unanswerable_type` | Required on every record; `null` or a string. Unanswerable records use `not_in_repo` or `false_premise`; answerable records use `null`. |

Optional top-level fields are `reference_answer` and `notes`, both strings.
Unknown fields are errors.

Each `relevant` item contains only:

| Field | Rule |
|---|---|
| `role` | Required: `primary` or `supporting`. |
| `file_path` | Required, non-empty relative POSIX path in the repository's `src_only` corpus. |
| `start_line`, `end_line` | Required 1-based inclusive integers; booleans, floats, and numeric strings are rejected. The range must be ordered and within the file. |
| `symbol` | Required; exact symbol `name` or `qualname` in the same file with a line range overlapping the ground truth, or `null`. |
| `order` | Optional integer, used only for primary `code_flow` locations. |

All integer fields use exact integer types: `true`, `false`, `1.0`, and `"1"` are
invalid. Extra fields are errors. Duplicate relevant items with the same
`file_path`, range, and role are errors.

## Categories and consistency

- ID group codes map to categories as follows: `sym` → `symbol_lookup`,
  `und` → `code_understanding`, `xfile` → `cross_file`, `flow` → `code_flow`,
  `cfg` → `configuration`, and `unans` → `unanswerable`.
- Answerable records require at least one primary location. Unanswerable records
  have `relevant: []`, `key_points: []`, and an
  `unanswerable_type` of `not_in_repo` or `false_premise`.
- `symbol_lookup` has exactly one primary; `code_understanding` has one or two;
  `cross_file` has at least two primary locations in at least two files;
  `code_flow` has at least three primary locations in at least two files; and
  `configuration` has one or two.
- Every primary `code_flow` location has a unique positive `order`, contiguous
  from 1. Supporting items and all other categories omit `order`.

## Corpus and line rules

`eval/corpus_profiles.yaml` is the source of truth for repository commit SHAs,
ID prefixes, and the `src_only` corpus. Only that profile is implemented.
Include and exclude values are relative path prefixes using `/`, each ending
in `/`. A Python file is in the corpus when its path ends in `.py`, starts with
an include prefix, and starts with no exclude prefix. Excludes win. Matching is
case-sensitive from the repository root; for example, `tests/` does not match
`src/flask/tests/file.py`. Enumeration includes ignored files and empty Python
files, but excludes symlinks and does not descend into symlink directories.

Files are decoded using Python's encoding detection and parsed as Python. A
decode or parse error is an error, not a skipped file. Line counts follow
Python's newline semantics for `\r\n`, `\r`, and `\n`; `\f` and other Unicode
line separators do not split lines. Empty text has zero lines, and a final
newline does not add a line. Empty files remain in the corpus but produce no
symbol records and cannot be ground truth. Whitespace-only, blank-line, and
comment-only files with at least one line are normal files.

Ground truth must point to a corpus file with a non-empty line count. If
`symbol` is not `null`, it must match an indexed module, class, function, or
method name or qualname in that file and overlap the ground-truth range.

## Checkout snapshot and `--skip-sha-check`

By default, the tools require `repo_root` to be the root of a Git repository,
and `HEAD` to equal the pinned SHA. The tracked corpus at that SHA is compared
with the on-disk corpus enumeration:

- `MISSING`: Python corpus files in the pinned Git tree but absent from disk.
- `EXTRA`: on-disk corpus files not in the pinned tree, including untracked
  and Git-ignored files.
- `MODIFIED`: common files whose Git-filtered content hash differs from the
  pinned blob.

Changes outside the corpus and under excluded prefixes do not affect snapshot
verification. A failed verification is a configuration error and prevents
dataset checks from running.

`--skip-sha-check` skips all Git checks: repository-root validation, Git
availability, `HEAD`, and snapshot integrity. It does not skip profile loading,
repo-root requirements, pinned `commit_sha` validation for every record,
corpus enumeration, decoding/parsing, schema validation, or ground-truth
validation.

## Completeness, warnings, and example

The completeness targets are 2 dev and 4 test questions for each
repository/category cell. `--phase dev|test|all` selects which target cells are
reported; it does not limit schema, consistency, source, or range validation.
Missing or excess counts are errors only with `--require-complete`.

The validator can warn about ranges longer than 150 lines, lexical-overlap
ratios outside 40–60% when a split has at least 10 records, lexical identifier
mismatches, and per-repository/per-split balance between unanswerable types.
Warnings do not change the exit code.

The following is one illustrative record only. It is not evaluation data and
must not be saved as a `.jsonl` dataset:

```json
{
  "id": "fa-sym-001",
  "schema_version": 1,
  "repo": "fastapi/fastapi",
  "commit_sha": "79cab5bed28b24d92c185d236feb33f5783dfeb3",
  "split": "dev",
  "category": "symbol_lookup",
  "lexical_overlap": "high",
  "question": "Where is the route registered?",
  "answerable": true,
  "relevant": [
    {
      "role": "primary",
      "file_path": "fastapi/routing.py",
      "start_line": 1,
      "end_line": 1,
      "symbol": null
    }
  ],
  "key_points": ["This is an illustrative schema example.", "Replace with verified ground truth."],
  "unanswerable_type": null
}
```
