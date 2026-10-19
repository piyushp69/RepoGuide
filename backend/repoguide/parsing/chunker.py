"""Turn a source file into retrieval chunks.

Python, JavaScript and TypeScript are parsed with tree-sitter into function-, method-
and class-level chunks (plus "module" chunks for top-level code such as imports).
Markdown is split by headings; every other text file falls back to line windows.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from ..ingest.languages import grammar_for
from . import js_extractor, python_extractor
from . import treesitter as ts
from .models import Chunk, Definition, Extraction, ParsedFile, SymbolDef, TopItem

log = logging.getLogger(__name__)
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class ChunkConfig:
    chunk_max_lines: int = 120
    class_inline_max_lines: int = 60
    window_lines: int = 60
    window_overlap: int = 10
    split_overlap: int = 3


def split_lines(text: str) -> list[str]:
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _split_range(start: int, end: int, max_lines: int, lines: list[str], overlap: int) -> list[tuple[int, int]]:
    """Split [start, end] into windows of at most ``max_lines``, cutting at blank lines when possible."""
    parts: list[tuple[int, int]] = []
    s = start
    while s <= end:
        e = min(s + max_lines - 1, end)
        if e < end:
            floor = s + int(max_lines * 0.75)
            for k in range(e, floor - 1, -1):
                if not lines[k - 1].strip():
                    e = k
                    break
        parts.append((s, e))
        if e >= end:
            break
        s = max(e + 1 - overlap, s + 1)
    return parts


class _Builder:
    def __init__(self, path: str, language: str, lines: list[str], cfg: ChunkConfig):
        self.path, self.language, self.lines, self.cfg = path, language, lines, cfg
        self.chunks: list[Chunk] = []

    def _trim(self, start: int, end: int) -> tuple[int, int] | None:
        start, end = max(1, start), min(len(self.lines), end)
        while start <= end and not self.lines[start - 1].strip():
            start += 1
        while end >= start and not self.lines[end - 1].strip():
            end -= 1
        return (start, end) if start <= end else None

    def add(self, start: int, end: int, symbol: str | None, symbol_type: str, max_lines: int,
            overlap: int, part_type: str | None = None) -> None:
        trimmed = self._trim(start, end)
        if trimmed is None:
            return
        start, end = trimmed
        parts = [(start, end)] if end - start + 1 <= max_lines else _split_range(start, end, max_lines, self.lines, overlap)
        for s, e in parts:
            self.chunks.append(Chunk(
                file_path=self.path, start_line=s, end_line=e, symbol=symbol,
                symbol_type=symbol_type if len(parts) == 1 or part_type is None else part_type,
                language=self.language, content="\n".join(self.lines[s - 1:e]),
            ))


def _emit_definition(builder: _Builder, definition: Definition, cfg: ChunkConfig) -> None:
    span = definition.end - definition.start + 1
    if definition.kind == "class" and definition.members and span > cfg.class_inline_max_lines:
        members = sorted(definition.members, key=lambda m: m.start)
        header_end = max(definition.def_start, members[0].start - 1)
        builder.add(definition.start, header_end, definition.name, "class", cfg.chunk_max_lines, cfg.split_overlap)
        for index, member in enumerate(members):
            # Gaps between methods (and a closing brace) belong to the preceding method.
            next_start = members[index + 1].start if index + 1 < len(members) else definition.end + 1
            member_end = max(member.end, next_start - 1)
            builder.add(member.start, member_end, f"{definition.name}.{member.name}", "method",
                        cfg.chunk_max_lines, cfg.split_overlap)
        return
    builder.add(definition.start, definition.end, definition.name, definition.kind,
                cfg.chunk_max_lines, cfg.split_overlap)


def _symbols_for(definition: Definition) -> list[SymbolDef]:
    symbols = [SymbolDef(
        name=definition.name, qualified_name=definition.name, kind=definition.kind,
        start_line=definition.def_start, end_line=definition.end, docstring=definition.docstring,
    )]
    for member in definition.members:
        symbols.append(SymbolDef(
            name=member.name, qualified_name=f"{definition.name}.{member.name}", kind="method",
            start_line=member.def_start, end_line=member.end, parent=definition.name,
            docstring=member.docstring,
        ))
    return symbols


def _code_chunks(path: str, language: str, lines: list[str], items: list[TopItem],
                 cfg: ChunkConfig) -> tuple[list[Chunk], list[SymbolDef]]:
    builder = _Builder(path, language, lines, cfg)
    symbols: list[SymbolDef] = []
    module_name = PurePosixPath(path).name
    leading = [(it.definition.start, it.definition.def_start) for it in items if it.definition]

    def absorbed(item: TopItem) -> bool:
        return item.is_comment and any(s <= item.start and item.end < d for s, d in leading)

    run: list[int] = []  # [start, end] of the current run of module-level code

    def flush() -> None:
        if run:
            builder.add(run[0], run[1], module_name, "module", cfg.window_lines, cfg.window_overlap)
            run.clear()

    for item in items:
        if item.definition is None:
            if absorbed(item):
                continue
            if run:
                run[1] = max(run[1], item.end)
            else:
                run.extend([item.start, item.end])
            continue
        flush()
        _emit_definition(builder, item.definition, cfg)
        for definition in [item.definition, *item.extra_definitions]:
            symbols.extend(_symbols_for(definition))
    flush()
    return builder.chunks, symbols


def _markdown_chunks(path: str, language: str, lines: list[str], cfg: ChunkConfig) -> list[Chunk]:
    sections: list[list] = []  # [start, end, heading]
    in_fence = False
    current_start, current_heading = 1, None
    for number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        match = None if in_fence else _HEADING.match(stripped)
        if match and number > current_start:
            sections.append([current_start, number - 1, current_heading])
            current_start, current_heading = number, match.group(2).strip()
        elif match:
            current_heading = match.group(2).strip()
    sections.append([current_start, len(lines), current_heading])

    # Merge sections with almost no body into the following one.
    merged: list[list] = []
    pending: list | None = None
    for section in sections:
        if pending is not None:
            section = [pending[0], section[1], pending[2] or section[2]]
            pending = None
        body = [ln for ln in lines[section[0] - 1:section[1]] if ln.strip()]
        if len(body) < 3 and section is not sections[-1]:
            pending = section
            continue
        merged.append(section)
    if pending is not None:
        merged.append(pending)

    builder = _Builder(path, language, lines, cfg)
    file_name = PurePosixPath(path).name
    for start, end, heading in merged:
        builder.add(start, end, heading or file_name, "section", cfg.window_lines, cfg.window_overlap)
    return builder.chunks


def _window_chunks(path: str, language: str, lines: list[str], cfg: ChunkConfig) -> list[Chunk]:
    builder = _Builder(path, language, lines, cfg)
    builder.add(1, len(lines), PurePosixPath(path).name, "file", cfg.window_lines, cfg.window_overlap,
                part_type="window")
    return builder.chunks


def parse_file(path: str, text: str, language: str, cfg: ChunkConfig | None = None) -> ParsedFile:
    """Parse one file's text into chunks, symbols, imports and calls."""
    cfg = cfg or ChunkConfig()
    lines = split_lines(text)
    grammar = grammar_for(path)
    if grammar and lines:
        try:
            tree = ts.parse(grammar, "\n".join(lines).encode("utf-8"))
            extraction: Extraction = (
                python_extractor.extract(tree.root_node) if grammar == "python"
                else js_extractor.extract(tree.root_node, grammar)
            )
            chunks, symbols = _code_chunks(path, language, lines, extraction.items, cfg)
            return ParsedFile(
                path=path, language=language, grammar=grammar, line_count=len(lines), chunks=chunks,
                symbols=symbols, imports=extraction.imports, calls=extraction.calls,
                default_export=extraction.default_export, has_main_guard=extraction.has_main_guard,
            )
        except Exception:  # a grammar bug must never stop indexing; fall back to line windows
            log.exception("tree-sitter extraction failed for %s; using line windows", path)
    if language == "markdown":
        chunks = _markdown_chunks(path, language, lines, cfg)
    else:
        chunks = _window_chunks(path, language, lines, cfg)
    return ParsedFile(path=path, language=language, grammar=None, line_count=len(lines), chunks=chunks,
                      symbols=[], imports=[], calls=[])
