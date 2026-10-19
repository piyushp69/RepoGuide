"""Discover indexable files in a project directory.

Respects nested ``.gitignore`` files and skips binaries, lock files, build output,
dependency folders, virtual environments, secrets and files above the size limit.
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

from . import languages

SNIFF_BYTES = 8192


@dataclass
class DiscoveredFile:
    rel_path: str  # POSIX-style path relative to the project root
    abs_path: Path
    size: int
    language: str


@dataclass
class WalkResult:
    files: list[DiscoveredFile] = field(default_factory=list)
    skipped: Counter = field(default_factory=Counter)
    truncated: bool = False


def _load_gitignore(path: Path) -> pathspec.GitIgnoreSpec | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return pathspec.GitIgnoreSpec.from_lines(lines)


def _is_ignored(rel_path: str, specs: list[tuple[str, pathspec.GitIgnoreSpec]]) -> bool:
    """Evaluate .gitignore specs from the root down; the deepest file with an opinion wins."""
    decision = False
    for base, spec in specs:
        sub = rel_path[len(base) + 1:] if base else rel_path
        result = spec.check_file(sub)
        if result.include is not None:
            decision = bool(result.include)
    return decision


def _looks_binary_or_minified(head: bytes, size: int) -> str | None:
    if b"\x00" in head:
        return "binary"
    # A single enormous first line is almost always minified or generated output.
    if size > SNIFF_BYTES and b"\n" not in head:
        return "generated"
    return None


def walk_project(root: Path, *, max_file_bytes: int, max_files: int) -> WalkResult:
    root = Path(root).resolve()
    result = WalkResult()
    specs: dict[str, pathspec.GitIgnoreSpec] = {}

    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        current = Path(dirpath)
        rel_dir = current.relative_to(root).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir

        gitignore = current / ".gitignore"
        if gitignore.is_file():
            spec = _load_gitignore(gitignore)
            if spec is not None:
                specs[rel_dir] = spec
        applicable = sorted(
            ((base, spec) for base, spec in specs.items()
             if base == "" or rel_dir == base or rel_dir.startswith(base + "/")),
            key=lambda item: item[0].count("/") + (1 if item[0] else 0),
        )

        kept_dirs = []
        for name in sorted(dirnames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = current / name
            if name in languages.IGNORED_DIR_NAMES or name.endswith(".egg-info"):
                result.skipped["ignored_dir"] += 1
                continue
            if full.is_symlink():
                result.skipped["symlink"] += 1
                continue
            if (full / "pyvenv.cfg").exists() or (full / "conda-meta").is_dir():
                result.skipped["virtualenv"] += 1
                continue
            if _is_ignored(rel + "/", applicable):
                result.skipped["gitignore"] += 1
                continue
            kept_dirs.append(name)
        dirnames[:] = kept_dirs

        for name in sorted(filenames):
            rel = f"{rel_dir}/{name}" if rel_dir else name
            full = current / name
            reason = None
            if full.is_symlink():
                reason = "symlink"
            elif languages.is_secret_file(name):
                reason = "secret"
            elif languages.is_lock_file(name):
                reason = "lockfile"
            elif languages.is_binary_name(name):
                reason = "binary"
            elif languages.is_generated_name(name):
                reason = "generated"
            elif languages.is_data_dump(name):
                reason = "data"
            elif _is_ignored(rel, applicable):
                reason = "gitignore"
            if reason:
                result.skipped[reason] += 1
                continue
            try:
                size = full.stat().st_size
            except OSError:
                result.skipped["unreadable"] += 1
                continue
            if size == 0:
                result.skipped["empty"] += 1
                continue
            if size > max_file_bytes:
                result.skipped["too_large"] += 1
                continue
            try:
                with full.open("rb") as handle:
                    head = handle.read(SNIFF_BYTES)
            except OSError:
                result.skipped["unreadable"] += 1
                continue
            sniff = _looks_binary_or_minified(head, size)
            if sniff:
                result.skipped[sniff] += 1
                continue
            if len(result.files) >= max_files:
                result.truncated = True
                result.skipped["over_file_limit"] += 1
                continue
            result.files.append(
                DiscoveredFile(rel_path=rel, abs_path=full, size=size, language=languages.detect_language(rel))
            )
    return result


def read_text(path: Path) -> tuple[bytes, str]:
    """Read a file and return (raw bytes, normalised text with '\\n' line endings)."""
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return raw, text
