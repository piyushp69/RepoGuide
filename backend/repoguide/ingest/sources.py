"""Resolve a user-supplied source (local folder or GitHub URL) to a directory on disk."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..errors import InvalidInputError

GITHUB_HTTPS = re.compile(
    r"^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)"
    r"(?:\.git)?(?:/tree/(?P<branch>[^?#]+?))?/?(?:[?#].*)?$"
)
GITHUB_SSH = re.compile(r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$")
SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class SourceError(InvalidInputError):
    """The source could not be resolved (bad path, bad URL, clone failure)."""


@dataclass
class SourceSpec:
    source_type: str  # "local" | "github"
    source: str  # the normalised user input
    name: str
    root_path: Path
    clone_url: str | None = None
    branch: str | None = None


def parse_source(raw: str, workspace_dir: Path) -> SourceSpec:
    """Classify user input as a GitHub repository or a local folder."""
    value = (raw or "").strip().strip('"').strip("'")
    if not value:
        raise SourceError("Enter a local folder path or a GitHub repository URL.")

    match = GITHUB_HTTPS.match(value) or GITHUB_SSH.match(value)
    if match:
        owner, repo = match.group("owner"), match.group("repo")
        branch = match.groupdict().get("branch")
        if repo.endswith(".git"):
            repo = repo[:-4]
        dest_name = f"{owner}__{repo}" + (f"__{SAFE_NAME.sub('_', branch)}" if branch else "")
        return SourceSpec(
            source_type="github",
            source=f"https://github.com/{owner}/{repo}" + (f"/tree/{branch}" if branch else ""),
            name=f"{owner}/{repo}",
            root_path=(workspace_dir / "github" / dest_name).resolve(),
            clone_url=f"https://github.com/{owner}/{repo}.git",
            branch=branch,
        )

    if value.lower().startswith(("http://", "https://", "git@")):
        raise SourceError("Only GitHub repository URLs (https://github.com/owner/repo) are supported.")

    path = Path(os.path.expandvars(os.path.expanduser(value)))
    if not path.is_absolute():
        raise SourceError("Use an absolute folder path, e.g. D:\\code\\my-app or /home/me/my-app.")
    path = path.resolve()
    if not path.exists():
        raise SourceError(f"Folder not found: {path}")
    if not path.is_dir():
        raise SourceError(f"Not a folder: {path}")
    return SourceSpec(source_type="local", source=str(path), name=path.name or str(path), root_path=path)


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"  # never hang waiting for credentials
    env["GCM_INTERACTIVE"] = "never"
    return env


def _run_git(args: list[str], cwd: Path | None, timeout: int) -> None:
    git = shutil.which("git")
    if not git:
        raise SourceError("git is not installed or not on PATH; it is required to clone GitHub repositories.")
    try:
        proc = subprocess.run(
            [git, *args], cwd=cwd, env=_git_env(), capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise SourceError(f"git {args[0]} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        message = detail[-1] if detail else f"exit code {proc.returncode}"
        raise SourceError(f"git {args[0]} failed: {message}")


def sync_github(spec: SourceSpec, timeout: int) -> None:
    """Clone the repository, or fetch the latest commit if it was cloned before."""
    assert spec.clone_url
    dest = spec.root_path
    if (dest / ".git").is_dir():
        ref = spec.branch or "HEAD"
        _run_git(["fetch", "--depth", "1", "origin", ref], cwd=dest, timeout=timeout)
        _run_git(["reset", "--hard", "FETCH_HEAD"], cwd=dest, timeout=timeout)
        return
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    args = ["clone", "--depth", "1", "--single-branch"]
    if spec.branch:
        args += ["--branch", spec.branch]
    args += ["--", spec.clone_url, str(dest)]
    _run_git(args, cwd=None, timeout=timeout)
