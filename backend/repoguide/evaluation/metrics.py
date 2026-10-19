"""Pure metric functions (no I/O) so they are easy to test."""

from __future__ import annotations

from typing import Any, Iterable, Sequence


def is_relevant(chunk: dict[str, Any], expected_file: str, symbol_ranges: Sequence[tuple[int, int]]) -> bool:
    """A retrieved chunk is relevant if it is in the expected file and overlaps the expected symbol.

    With no symbol ranges (file-level question, or symbol not found in the index) the file
    match alone counts.
    """
    if chunk["file_path"] != expected_file:
        return False
    if not symbol_ranges:
        return True
    return any(chunk["start_line"] <= end and chunk["end_line"] >= start for start, end in symbol_ranges)


def first_relevant_rank(ranked: Sequence[dict[str, Any]], expected_file: str,
                        symbol_ranges: Sequence[tuple[int, int]]) -> int | None:
    for rank, chunk in enumerate(ranked, start=1):
        if is_relevant(chunk, expected_file, symbol_ranges):
            return rank
    return None


def hit_at_k(rank: int | None, k: int) -> bool:
    return rank is not None and rank <= k


def reciprocal_rank(rank: int | None, cutoff: int | None = None) -> float:
    if rank is None or (cutoff is not None and rank > cutoff):
        return 0.0
    return 1.0 / rank


def mean(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(sum(items) / len(items), 4) if items else None


def check_citation(citation: dict[str, Any], file_lines: dict[str, list[str]],
                   indexed_content: str | None = None) -> tuple[bool, str]:
    """Validate a file/line citation against the files on disk.

    ``file_lines`` maps project-relative paths to the file's current lines. When the
    indexed chunk text is given, the cited lines must still match it (index not stale).
    """
    path = citation.get("file_path")
    if path not in file_lines:
        return False, "file does not exist"
    lines = file_lines[path]
    start, end = citation.get("start_line"), citation.get("end_line")
    if start is None:
        return True, "ok (file only)"
    end = end if end is not None else start
    if start < 1 or end < start:
        return False, "invalid line range"
    if end > len(lines):
        return False, f"line {end} is past the end of the file ({len(lines)} lines)"
    if indexed_content is not None:
        current = "\n".join(lines[start - 1:end]).strip()
        if current and indexed_content.strip() and not indexed_content.strip().startswith(current[:200]):
            return False, "cited lines changed since indexing"
    return True, "ok"


def mention_matches(path: str, known_paths: Iterable[str]) -> list[str]:
    """Project files that a path mentioned in an answer (possibly partial, e.g. 'orders.py') can refer to."""
    known = list(known_paths)
    if path in known:
        return [path]
    return [p for p in known if p.endswith("/" + path)]


def check_mention(mention: dict[str, Any], known_paths: Iterable[str],
                  file_lines: dict[str, list[str]]) -> tuple[str, bool, str]:
    """Validate a path the answer mentions; returns (resolved path, valid, reason).

    A bare name such as 'main.py' can belong to several files; it is valid when it fits one of them.
    """
    matches = mention_matches(mention["path"], known_paths)
    if not matches:
        return mention["path"], False, "file does not exist"
    results = [(path, *check_citation({**mention, "file_path": path}, file_lines)) for path in matches]
    if len(results) == 1:
        return results[0]
    fitting = [r for r in results if r[1]]
    # Keep the name as written unless the line range singles out one of the files.
    path, valid, reason = fitting[0] if len(fitting) == 1 else (mention["path"], bool(fitting), (fitting or results)[0][2])
    return path, valid, f"{reason} (name shared by {len(matches)} files)"
