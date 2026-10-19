"""File discovery (.gitignore, skipped file types) and source resolution."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from repoguide.ingest.sources import SourceError, SourceSpec, parse_source, sync_github
from repoguide.ingest.walker import walk_project


def write(root: Path, rel: str, content: str | bytes = "x = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def test_walker_respects_gitignore_and_skips_noise(tmp_path: Path):
    root = tmp_path / "repo"
    write(root, ".gitignore", "*.txt\nsecret_dir/\n/generated.py\n!keep.txt\n")
    write(root, "src/app.py")
    write(root, "src/.gitignore", "local_only.py\n")
    write(root, "src/local_only.py")
    write(root, "src/nested/generated.py")  # root-anchored pattern must not match here
    write(root, "generated.py")
    write(root, "notes.txt", "ignored by *.txt")
    write(root, "keep.txt", "re-included by !keep.txt")
    write(root, "debug.log", "log files are skipped as data dumps")
    write(root, "secret_dir/a.py")
    write(root, "node_modules/lib/index.js", "module.exports = 1;")
    write(root, "dist/bundle.js", "var a;")
    write(root, "myenv/pyvenv.cfg", "home = x")
    write(root, "myenv/lib/site.py")
    write(root, "package-lock.json", "{}")
    write(root, "poetry.lock", "")
    write(root, "logo.png", b"\x89PNG\r\n\x1a\n\x00\x00")
    write(root, "data.bin2", b"abc\x00def")
    write(root, "app.min.js", "var a=1;")
    write(root, ".env", "SECRET=1")
    write(root, ".env.example", "SECRET=")
    write(root, "big.py", "x = 1\n" * 50_000)
    write(root, "empty.py", "")

    result = walk_project(root, max_file_bytes=64 * 1024, max_files=100)
    paths = sorted(f.rel_path for f in result.files)
    assert paths == [".env.example", ".gitignore", "keep.txt", "src/.gitignore", "src/app.py",
                     "src/nested/generated.py"]
    skipped = result.skipped
    assert skipped["gitignore"] == 4  # notes.txt, generated.py, secret_dir/, src/local_only.py
    assert skipped["data"] == 1
    assert skipped["ignored_dir"] >= 2 and skipped["virtualenv"] == 1
    assert skipped["lockfile"] == 2 and skipped["binary"] == 2 and skipped["generated"] == 1
    assert skipped["secret"] == 1 and skipped["too_large"] == 1 and skipped["empty"] == 1
    languages = {f.rel_path: f.language for f in result.files}
    assert languages["src/app.py"] == "python" and languages[".env.example"] == "dotenv"


def test_walker_file_limit(tmp_path: Path):
    for i in range(5):
        write(tmp_path, f"m{i}.py")
    result = walk_project(tmp_path, max_file_bytes=1024, max_files=3)
    assert len(result.files) == 3 and result.truncated and result.skipped["over_file_limit"] == 2


@pytest.mark.parametrize("url, owner_repo, branch", [
    ("https://github.com/pallets/flask", "pallets/flask", None),
    ("https://github.com/pallets/flask.git", "pallets/flask", None),
    ("github.com/pallets/flask/", "pallets/flask", None),
    ("https://github.com/pallets/flask/tree/3.0.x", "pallets/flask", "3.0.x"),
    ("git@github.com:pallets/flask.git", "pallets/flask", None),
])
def test_parse_github_urls(tmp_path: Path, url: str, owner_repo: str, branch: str | None):
    spec = parse_source(url, tmp_path)
    assert spec.source_type == "github" and spec.name == owner_repo and spec.branch == branch
    assert spec.clone_url == f"https://github.com/{owner_repo}.git"
    assert spec.root_path.parent == (tmp_path / "github").resolve()


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_clone_then_fetch_updates(tmp_path: Path):
    """The clone/update path, exercised against a local repository (no network)."""
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(*args: str, cwd: Path = origin) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    write(origin, "app.py", "def main():\n    return 1\n")
    git("add", ".")
    git("commit", "-m", "first")
    spec = SourceSpec(source_type="github", source="https://github.com/example/app", name="example/app",
                      root_path=tmp_path / "workspace" / "example__app", clone_url=origin.as_uri())
    sync_github(spec, timeout=60)
    assert (spec.root_path / "app.py").is_file()

    write(origin, "new_module.py", "X = 1\n")
    git("add", ".")
    git("commit", "-m", "second")
    sync_github(spec, timeout=60)  # existing clone: fetch + reset to the new commit
    assert (spec.root_path / "new_module.py").is_file()

    bad = SourceSpec(source_type="github", source="x", name="x", root_path=tmp_path / "workspace" / "missing",
                     clone_url=(tmp_path / "does-not-exist").as_uri())
    with pytest.raises(SourceError):
        sync_github(bad, timeout=60)


def test_parse_local_paths(tmp_path: Path):
    spec = parse_source(str(tmp_path), tmp_path / "ws")
    assert spec.source_type == "local" and spec.root_path == tmp_path.resolve()
    with pytest.raises(SourceError):
        parse_source(str(tmp_path / "missing"), tmp_path)
    with pytest.raises(SourceError):
        parse_source("relative/path", tmp_path)
    with pytest.raises(SourceError):
        parse_source("https://gitlab.com/a/b", tmp_path)
    with pytest.raises(SourceError):
        parse_source("   ", tmp_path)
