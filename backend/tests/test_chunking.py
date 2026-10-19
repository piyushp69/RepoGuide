"""Tree-sitter chunking for Python / JavaScript / TypeScript and the text fallbacks."""

from __future__ import annotations

from textwrap import dedent

from repoguide.ingest.walker import read_text, walk_project
from repoguide.parsing import parse_file
from repoguide.parsing.chunker import ChunkConfig, split_lines

from .conftest import SAMPLE_REPO


def by_symbol(parsed):
    return {c.symbol: c for c in parsed.chunks}


PY_SOURCE = dedent('''\
    """Module docstring."""
    import os
    from .models import User as U, Order


    # Adds two numbers.
    def add(a, b):
        """Return a + b."""
        return a + b


    @decorator
    def wrapped():
        return add(1, 2)


    class Small:
        """A tiny class."""

        def method(self):
            return self.other()

        def other(self):
            return 1


    if __name__ == "__main__":
        wrapped()
    ''')


def test_python_function_and_class_chunks():
    parsed = parse_file("pkg/mod.py", PY_SOURCE, "python")
    chunks = by_symbol(parsed)
    assert parsed.grammar == "python"
    assert parsed.has_main_guard
    # Leading comment is attached to the function chunk; the symbol starts at the def line.
    assert (chunks["add"].start_line, chunks["add"].end_line, chunks["add"].symbol_type) == (6, 9, "function")
    assert chunks["wrapped"].start_line == 12  # decorator included
    assert chunks["Small"].symbol_type == "class" and chunks["Small"].end_line == 24
    module_chunks = [c for c in parsed.chunks if c.symbol_type == "module"]
    assert module_chunks[0].start_line == 1 and "import os" in module_chunks[0].content
    assert module_chunks[-1].content.startswith('if __name__ == "__main__":')

    symbols = {s.qualified_name: s for s in parsed.symbols}
    assert symbols["add"].start_line == 7 and symbols["add"].docstring == "Return a + b."
    assert symbols["Small.method"].parent == "Small" and symbols["Small.method"].kind == "method"
    assert symbols["Small"].docstring == "A tiny class."


def test_python_imports_and_calls():
    parsed = parse_file("pkg/mod.py", PY_SOURCE, "python")
    imports = {(i.module, i.level): i for i in parsed.imports}
    assert imports[("os", 0)].namespace == "os"
    assert imports[("models", 1)].names == [["User", "U"], ["Order", "Order"]]
    calls = {(c.receiver, c.name) for c in parsed.calls}
    assert (None, "add") in calls and ("self", "other") in calls and (None, "wrapped") in calls


def test_large_class_is_split_into_header_and_methods():
    methods = "\n".join(f"    def m{i}(self):\n        x = {i}\n        return x\n" for i in range(12))
    source = f'class Big:\n    """Big class."""\n    limit = 3\n\n{methods}'
    parsed = parse_file("big.py", source, "python", ChunkConfig(class_inline_max_lines=20))
    kinds = [(c.symbol, c.symbol_type) for c in parsed.chunks]
    assert kinds[0] == ("Big", "class")
    # The header chunk holds the class line, docstring and class attributes, up to the first method.
    assert parsed.chunks[0].end_line == 3 and "limit = 3" in parsed.chunks[0].content
    assert [s for s, t in kinds if t == "method"] == [f"Big.m{i}" for i in range(12)]
    # Method chunks are contiguous and do not overlap.
    method_chunks = [c for c in parsed.chunks if c.symbol_type == "method"]
    for first, second in zip(method_chunks, method_chunks[1:]):
        assert first.end_line < second.start_line


def test_long_function_is_split_within_limits():
    body = "\n".join(f"    value_{i} = {i}" + ("\n" if i % 10 == 9 else "") for i in range(300))
    source = f"def long_function():\n{body}\n    return value_0\n"
    cfg = ChunkConfig(chunk_max_lines=80)
    parsed = parse_file("long.py", source, "python", cfg)
    parts = [c for c in parsed.chunks if c.symbol == "long_function"]
    assert len(parts) >= 4
    assert all(c.end_line - c.start_line + 1 <= 80 for c in parts)
    assert parts[0].start_line == 1 and parts[-1].end_line == len(split_lines(source))
    for first, second in zip(parts, parts[1:]):
        assert second.start_line <= first.end_line + 1  # no gaps


TS_SOURCE = dedent('''\
    import React, { useState } from "react";
    import * as api from "./api";
    const helper = require("./helper");

    export interface Props { title: string }
    export type Mode = "a" | "b";
    export enum Color { Red, Blue }

    /** Adds things. */
    export function add(a: number, b: number): number {
      return helper.sum(a, b);
    }

    export const double = (x: number) => x * 2;

    export class Store {
      private items: string[] = [];
      push(item: string) { this.items.push(item); api.save(item); }
      get size() { return this.items.length; }
    }

    export default function App({ title }: Props) {
      const [n] = useState(0);
      return <Panel title={title} count={double(n)} />;
    }
    ''')


def test_typescript_tsx_definitions_imports_and_jsx_calls():
    parsed = parse_file("src/App.tsx", TS_SOURCE, "typescript")
    assert parsed.grammar == "tsx"
    types = {c.symbol: c.symbol_type for c in parsed.chunks}
    assert types["Props"] == "interface" and types["Mode"] == "type" and types["Color"] == "enum"
    assert types["add"] == "function" and types["double"] == "function"
    assert types["Store"] == "class" and types["App"] == "function"
    assert by_symbol(parsed)["add"].start_line == 9  # JSDoc comment included
    assert parsed.default_export == "App"
    symbols = {s.qualified_name: s for s in parsed.symbols}
    assert symbols["add"].docstring == "Adds things."
    assert {"Store.push", "Store.size"} <= set(symbols)

    imports = {i.module: i for i in parsed.imports}
    assert imports["react"].names == [["default", "React"], ["useState", "useState"]]
    assert imports["./api"].namespace == "api"
    assert imports["./helper"].namespace == "helper"
    calls = {(c.receiver, c.name, c.kind) for c in parsed.calls}
    assert (None, "Panel", "jsx") in calls
    assert ("api", "save", "call") in calls and ("helper", "sum", "call") in calls


def test_javascript_commonjs_and_default_export():
    source = "module.exports.handler = function (req) { return build(req); };\nexport default () => 1;\n"
    parsed = parse_file("lib/handler.js", source, "javascript")
    assert {c.symbol for c in parsed.chunks} >= {"handler", "default"}
    assert parsed.default_export == "default"


def test_markdown_sections_ignore_headings_inside_code_fences():
    source = "# Title\nIntro line one.\nIntro line two.\nIntro three.\n\n## Usage\nRun it:\n```bash\n# not a heading\nmake run\n```\nMore text.\n"
    parsed = parse_file("README.md", source, "markdown")
    assert [c.symbol for c in parsed.chunks] == ["Title", "Usage"]
    assert "# not a heading" in parsed.chunks[1].content


def test_window_fallback_for_other_text_files():
    source = "\n".join(f"key_{i} = {i}" for i in range(150))
    parsed = parse_file("conf/settings.ini", source, "ini", ChunkConfig(window_lines=60, window_overlap=10))
    assert parsed.grammar is None
    assert all(c.symbol_type == "window" and c.symbol == "settings.ini" for c in parsed.chunks)
    assert parsed.chunks[0].start_line == 1 and parsed.chunks[-1].end_line == 150
    for first, second in zip(parsed.chunks, parsed.chunks[1:]):
        assert second.start_line <= first.end_line  # windows overlap
    small = parse_file("requirements.txt", "flask\n", "pip-requirements")
    assert [(c.start_line, c.end_line, c.symbol_type) for c in small.chunks] == [(1, 1, "file")]


def test_syntax_errors_still_produce_chunks():
    parsed = parse_file("broken.py", "def ok():\n    return 1\n\ndef broken(:\n    pass\n", "python")
    assert parsed.chunks and any(c.symbol == "ok" for c in parsed.chunks)


def test_every_sample_chunk_matches_the_file_lines():
    """Chunk metadata is exact: content equals the file's lines at the stated range."""
    walk = walk_project(SAMPLE_REPO, max_file_bytes=512 * 1024, max_files=1000)
    assert len(walk.files) == 15
    for discovered in walk.files:
        _, text = read_text(discovered.abs_path)
        lines = split_lines(text)
        parsed = parse_file(discovered.rel_path, text, discovered.language)
        assert parsed.chunks, discovered.rel_path
        for chunk in parsed.chunks:
            assert 1 <= chunk.start_line <= chunk.end_line <= len(lines)
            assert chunk.content == "\n".join(lines[chunk.start_line - 1:chunk.end_line])
            assert chunk.file_path == discovered.rel_path and chunk.language == discovered.language
        for symbol in parsed.symbols:
            assert any(c.start_line <= symbol.start_line <= c.end_line for c in parsed.chunks), symbol
