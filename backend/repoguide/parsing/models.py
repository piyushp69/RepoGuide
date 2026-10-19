"""Data structures produced by the parser."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chunk:
    file_path: str
    start_line: int  # 1-based, inclusive
    end_line: int  # 1-based, inclusive
    symbol: str | None
    symbol_type: str  # function | method | class | interface | type | enum | namespace | module | section | file | window
    language: str
    content: str


@dataclass
class SymbolDef:
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int
    parent: str | None = None
    docstring: str | None = None


@dataclass
class ImportRef:
    module: str  # "app.models", "./api", "react"
    line: int
    level: int = 0  # Python relative-import level (number of leading dots)
    names: list[list[str]] = field(default_factory=list)  # [[imported_name, local_name]]
    namespace: str | None = None  # local name bound to the whole module
    reexport: bool = False
    dynamic: bool = False


@dataclass
class CallRef:
    name: str
    receiver: str | None
    line: int
    kind: str = "call"  # call | new | jsx


@dataclass
class ParsedFile:
    path: str
    language: str
    grammar: str | None
    line_count: int
    chunks: list[Chunk]
    symbols: list[SymbolDef]
    imports: list[ImportRef]
    calls: list[CallRef]
    default_export: str | None = None
    has_main_guard: bool = False

    def graph_payload(self) -> dict[str, Any]:
        """The parts of the parse that the graph builder needs (stored as JSON per file)."""
        return {
            "language": self.language,
            "grammar": self.grammar,
            "symbols": [asdict(s) for s in self.symbols],
            "imports": [asdict(i) for i in self.imports],
            "calls": [asdict(c) for c in self.calls],
            "default_export": self.default_export,
            "has_main_guard": self.has_main_guard,
        }


@dataclass
class Definition:
    """A definition found by an extractor, before it is turned into chunks."""

    name: str
    kind: str
    start: int  # chunk start: includes decorators and adjacent leading comments
    def_start: int  # first line of the definition itself (incl. decorators)
    end: int
    docstring: str | None = None
    members: list["Definition"] = field(default_factory=list)


@dataclass
class TopItem:
    """A top-level syntax node: either a definition or plain module code."""

    start: int
    end: int
    definition: Definition | None = None
    extra_definitions: list[Definition] = field(default_factory=list)
    is_comment: bool = False


@dataclass
class Extraction:
    items: list[TopItem]
    imports: list[ImportRef]
    calls: list[CallRef]
    default_export: str | None = None
    has_main_guard: bool = False
