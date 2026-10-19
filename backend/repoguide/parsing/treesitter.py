"""Tree-sitter grammar registry and node helpers."""

from __future__ import annotations

from functools import lru_cache

import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree

_QUERY_SOURCES = {
    "python": "(call) @call (import_statement) @import (import_from_statement) @import",
    "javascript": (
        "(call_expression) @call (new_expression) @new (jsx_opening_element) @jsx "
        "(jsx_self_closing_element) @jsx (import_statement) @import (export_statement) @export"
    ),
    "tsx": (
        "(call_expression) @call (new_expression) @new (jsx_opening_element) @jsx "
        "(jsx_self_closing_element) @jsx (import_statement) @import (export_statement) @export"
    ),
    "typescript": "(call_expression) @call (new_expression) @new (import_statement) @import (export_statement) @export",
}


@lru_cache(maxsize=None)
def get_language(grammar: str) -> Language:
    if grammar == "python":
        return Language(tree_sitter_python.language())
    if grammar == "javascript":
        return Language(tree_sitter_javascript.language())
    if grammar == "typescript":
        return Language(tree_sitter_typescript.language_typescript())
    if grammar == "tsx":
        return Language(tree_sitter_typescript.language_tsx())
    raise ValueError(f"No tree-sitter grammar for {grammar!r}")


@lru_cache(maxsize=None)
def get_query(grammar: str) -> Query:
    return Query(get_language(grammar), _QUERY_SOURCES[grammar])


def parse(grammar: str, source: bytes) -> Tree:
    # Parsers are cheap and not thread-safe, so build one per call.
    return Parser(get_language(grammar)).parse(source)


def captures(grammar: str, node: Node) -> dict[str, list[Node]]:
    result = QueryCursor(get_query(grammar)).captures(node)
    for nodes in result.values():
        nodes.sort(key=lambda n: n.start_byte)
    return result


def text(node: Node | None) -> str:
    if node is None or node.text is None:
        return ""
    return node.text.decode("utf-8", errors="replace")


def start_line(node: Node) -> int:
    return node.start_point.row + 1


def end_line(node: Node) -> int:
    row = node.end_point.row
    if node.end_point.column == 0 and row > node.start_point.row:
        row -= 1
    return row + 1


def _is_file_header(node: Node) -> bool:
    """Comments that start on the first line of a file describe the file, not the next symbol."""
    return node.type == "comment" and node.start_point.row == 0


def leading_start(node: Node, extra_types: tuple[str, ...] = ()) -> int:
    """First line of the node including directly preceding comments (and decorators)."""
    start = start_line(node)
    prev = node.prev_named_sibling
    while prev is not None and (prev.type == "comment" or prev.type in extra_types):
        if end_line(prev) < start - 1 or _is_file_header(prev):
            break
        start = start_line(prev)
        prev = prev.prev_named_sibling
    return start


def clean_comment(raw: str) -> str:
    """Strip comment markers from a // or /* */ comment block."""
    lines = []
    for line in raw.strip().splitlines():
        line = line.strip()
        for prefix in ("/**", "/*", "*/", "//", "#"):
            if line.startswith(prefix):
                line = line[len(prefix):]
        if line.endswith("*/"):
            line = line[:-2]
        line = line.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if line:
            lines.append(line)
    return " ".join(lines)


def preceding_comment(node: Node) -> str | None:
    """Text of comments directly above ``node`` (JSDoc and line comments)."""
    parts: list[str] = []
    prev = node.prev_named_sibling
    expected = start_line(node) - 1
    while prev is not None and prev.type == "comment" and end_line(prev) >= expected \
            and not _is_file_header(prev):
        parts.insert(0, clean_comment(text(prev)))
        expected = start_line(prev) - 1
        prev = prev.prev_named_sibling
    doc = " ".join(p for p in parts if p).strip()
    return doc or None
