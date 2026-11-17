from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

GITHUB_REPO_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
DEFAULT_CLONE_TIMEOUT_SECONDS = 120


class CloneError(RuntimeError):
    pass


def validate_repo_url(repo_url: str) -> str:
    value = repo_url.strip()
    if not GITHUB_REPO_RE.fullmatch(value) or value.endswith(".git"):
        raise ValueError(
            "repo_url must be https://github.com/<owner>/<repo> without .git or trailing slash"
        )
    return value


def git_output(args: list[str], *, cwd: Path | None = None, timeout: int = 15) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"},
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise CloneError(f"git command failed: {exc}") from exc
    return result.stdout.strip()


def resolve_commit_sha(repo_root: str | Path) -> str:
    root = Path(repo_root).resolve(strict=True)
    sha = git_output(["rev-parse", "HEAD"], cwd=root)
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise CloneError("Git returned an invalid commit SHA")
    return sha


def clone_repository(
    repo_url: str,
    destination: str | Path,
    timeout: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
    ref: str | None = None,
) -> str:
    url = validate_repo_url(repo_url)
    target = Path(destination).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and any(target.iterdir()):
        raise CloneError(f"Clone destination is not empty: {target}")
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }
    try:
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--no-recurse-submodules",
                url,
                str(target),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        raise CloneError(f"Clone timed out after {timeout} seconds") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").lower()
        if "repository not found" in stderr or "not found" in stderr:
            raise CloneError(
                "GitHub repository was not found or is not public"
            ) from exc
        raise CloneError("Unable to clone the requested GitHub repository") from exc
    except OSError as exc:
        raise CloneError("Unable to start Git while cloning the repository") from exc
    if ref:
        try:
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", ref],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            subprocess.run(
                ["git", "checkout", "--detach", "FETCH_HEAD"],
                cwd=target,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise CloneError(f"Fetching ref timed out after {timeout} seconds") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").lower()
            if "not found" in stderr or "couldn't find remote ref" in stderr:
                raise CloneError("Requested Git reference was not found") from exc
            raise CloneError("Unable to fetch the requested Git reference") from exc
        except OSError as exc:
            raise CloneError(
                "Unable to start Git while fetching the requested ref"
            ) from exc
    return resolve_commit_sha(target)
