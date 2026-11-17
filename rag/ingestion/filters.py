from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
    "vendor",
    "tests",
    "test",
    "docs",
    "examples",
    "docs_src",
}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
}
SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".keystore"}


class RepositoryLimitError(ValueError):
    """Raised when a repository exceeds a configured ingestion limit."""


@dataclass(frozen=True)
class FilterLimits:
    max_files: int = 2_000
    max_file_bytes: int = 256 * 1024
    max_total_bytes: int = 50 * 1024 * 1024
    allowed_extensions: frozenset[str] = frozenset({".py"})


@dataclass
class FilterReport:
    files: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)
    total_bytes: int = 0

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


def is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in SENSITIVE_NAMES
        or lowered.startswith((".env.", "id_rsa", "id_ed25519"))
        or lowered.endswith(tuple(SENSITIVE_SUFFIXES))
        or "secret" in lowered
        and lowered.endswith((".json", ".yaml", ".yml", ".toml"))
    )


def enumerate_python_files(
    repo_root: str | Path, limits: FilterLimits | None = None
) -> FilterReport:
    """Return sorted, safe, relative Python paths and a filtering summary."""
    root = Path(repo_root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Repository root is not a directory: {root}")
    limits = limits or FilterLimits()
    report = FilterReport()

    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if name not in EXCLUDED_DIRS and not (current_path / name).is_symlink()
        )
        for name in sorted(names):
            path = current_path / name
            if path.is_symlink():
                report.skip("symlink")
                continue
            if is_sensitive_name(name):
                report.skip("sensitive_name")
                continue
            if path.suffix.lower() not in limits.allowed_extensions:
                report.skip("extension")
                continue
            try:
                size = path.stat().st_size
            except OSError:
                report.skip("stat_error")
                continue
            if size > limits.max_file_bytes:
                report.skip("file_too_large")
                continue
            relative = path.relative_to(root).as_posix()
            if relative.startswith("/") or ".." in Path(relative).parts:
                report.skip("unsafe_path")
                continue
            if len(report.files) + 1 > limits.max_files:
                raise RepositoryLimitError(
                    f"Repository exceeds max_files={limits.max_files}"
                )
            if report.total_bytes + size > limits.max_total_bytes:
                raise RepositoryLimitError(
                    f"Repository exceeds max_total_bytes={limits.max_total_bytes}"
                )
            report.files.append(relative)
            report.total_bytes += size

    report.files.sort()
    return report
