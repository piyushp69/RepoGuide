"""Resolve imports and calls across a project into graph edges.

Import resolution follows Python and Node/TypeScript conventions (relative imports,
packages, source roots, index files, tsconfig ``paths`` aliases). Call resolution is
best effort: exact for local, imported and ``self``/``this`` calls, heuristic (lower
confidence) when a name is unique in the project.
"""

from __future__ import annotations

import logging
import posixpath
import sys
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ..util import loads_jsonc

log = logging.getLogger(__name__)

JS_EXTENSIONS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".vue", ".svelte", ".d.ts")
JS_SOURCE_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
STDLIB = set(getattr(sys, "stdlib_module_names", ()))

# Names too generic to link by "unique name in project" heuristics.
COMMON_NAMES = {
    "get", "set", "add", "update", "delete", "remove", "run", "start", "stop", "init", "main", "create",
    "load", "save", "read", "write", "open", "close", "send", "handle", "process", "call", "apply", "push",
    "pop", "append", "extend", "insert", "map", "filter", "reduce", "find", "forEach", "join", "split",
    "format", "replace", "strip", "keys", "values", "items", "then", "catch", "finally", "log", "error",
    "info", "debug", "warning", "warn", "print", "toString", "json", "render", "emit", "on", "off", "next",
    "reset", "clear", "copy", "sort", "index", "count", "execute", "fetchone", "fetchall", "commit",
    "rollback", "cursor", "query", "response", "use", "__init__", "constructor", "super", "len", "str",
    "int", "float", "dict", "list", "tuple", "bool", "range", "enumerate", "zip", "isinstance", "getattr",
    "setattr", "hasattr", "sum", "min", "max", "abs", "any", "all", "sorted", "reversed", "iter", "type",
    "object", "id", "hash", "repr", "property", "fetch", "require", "encode", "decode", "lower", "upper",
    "startswith", "endswith", "exists", "test", "it", "describe", "expect", "length", "size", "value",
    "default", "setState", "useState", "useEffect", "useMemo", "useCallback", "useRef", "resolve", "reject",
    "listen", "connect", "parse", "stringify", "assign", "includes", "indexOf", "slice", "splice", "concat",
}


@dataclass
class FileInfo:
    path: str
    language: str
    grammar: str | None
    symbols: list[dict[str, Any]]
    imports: list[dict[str, Any]]
    calls: list[dict[str, Any]]
    default_export: str | None = None
    has_main_guard: bool = False


@dataclass
class Edge:
    kind: str
    src_file: str
    src_symbol: str | None = None
    src_chunk_id: str | None = None
    dst_file: str | None = None
    dst_symbol: str | None = None
    dst_chunk_id: str | None = None
    external: str | None = None
    line: int | None = None
    confidence: float = 1.0


@dataclass
class _Symbol:
    file: str
    name: str
    qualified_name: str
    kind: str
    start: int
    end: int
    parent: str | None
    chunk_id: str | None


class ChunkLocator:
    """Find the most specific chunk covering a line."""

    def __init__(self, chunks: Iterable[dict[str, Any]]):
        self._by_file: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
        for chunk in chunks:
            self._by_file[chunk["file_path"]].append((chunk["start_line"], chunk["end_line"], chunk["id"]))
        for ranges in self._by_file.values():
            ranges.sort()
        self._starts = {f: [r[0] for r in ranges] for f, ranges in self._by_file.items()}

    def chunk_at(self, file: str, line: int) -> str | None:
        ranges = self._by_file.get(file)
        if not ranges:
            return None
        best: tuple[int, str] | None = None
        upto = bisect_right(self._starts[file], line)
        for start, end, chunk_id in ranges[:upto]:
            if start <= line <= end:
                span = end - start
                if best is None or span < best[0]:
                    best = (span, chunk_id)
        return best[1] if best else None


class PythonModules:
    def __init__(self, paths: Iterable[str]):
        py_files = [p for p in paths if p.endswith((".py", ".pyi"))]
        self.files = set(py_files)
        packages = {posixpath.dirname(p) for p in py_files if posixpath.basename(p) in ("__init__.py", "__init__.pyi")}
        self.by_name: dict[str, list[str]] = defaultdict(list)
        for path in py_files:
            parts = path.rsplit(".", 1)[0].split("/")
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                source_root = "/".join(parts[:i])
                # A directory that is itself a package cannot be a source root for its children.
                if i > 0 and source_root in packages:
                    continue
                self.by_name[".".join(parts[i:])].append(path)

    def _module_file(self, dotted_path: str) -> str | None:
        for candidate in (f"{dotted_path}.py", f"{dotted_path}/__init__.py", f"{dotted_path}.pyi"):
            if candidate in self.files:
                return candidate
        return None

    def resolve(self, module: str, importer: str, level: int = 0) -> str | None:
        if level > 0:
            base = posixpath.dirname(importer)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            target = "/".join(p for p in [base, module.replace(".", "/")] if p)
            return self._module_file(target)
        if not module:
            return None
        candidates = self.by_name.get(module, [])
        if not candidates:
            return None
        importer_dir = posixpath.dirname(importer)
        if module.split(".")[0] in STDLIB:
            # Only a sibling file can shadow a standard-library module.
            siblings = [c for c in candidates if posixpath.dirname(c) == importer_dir]
            return siblings[0] if siblings else None
        if len(candidates) == 1:
            return candidates[0]

        def closeness(candidate: str) -> tuple[int, int]:
            common = posixpath.commonpath([importer_dir or ".", posixpath.dirname(candidate) or "."]) \
                if importer_dir and posixpath.dirname(candidate) else ""
            return (-len(common), len(candidate))

        return sorted(candidates, key=closeness)[0]


class JsModules:
    def __init__(self, paths: Iterable[str], root: Path | None):
        self.files = set(paths)
        self.configs: list[tuple[str, str, dict[str, list[str]]]] = []  # (config_dir, base_url_dir, paths)
        if root is not None:
            for path in sorted(self.files):
                name = posixpath.basename(path)
                if not ((name.startswith("tsconfig") and name.endswith(".json")) or name == "jsconfig.json"):
                    continue
                try:
                    data = loads_jsonc((root / path).read_text(encoding="utf-8", errors="replace"))
                except (OSError, ValueError):
                    continue
                options = (data or {}).get("compilerOptions") or {}
                config_dir = posixpath.dirname(path)
                base_url = options.get("baseUrl")
                base_dir = posixpath.normpath(posixpath.join(config_dir, base_url)) if base_url else config_dir
                base_dir = "" if base_dir == "." else base_dir
                paths_map = options.get("paths") or {}
                if base_url or paths_map:
                    self.configs.append((config_dir, base_dir, paths_map))

    def _probe(self, base: str) -> str | None:
        if base.startswith("..") or base.startswith("/"):
            return None
        base = "" if base == "." else base
        candidates = [base]
        stem, ext = posixpath.splitext(base)
        if ext in (".js", ".jsx", ".mjs", ".cjs"):
            candidates += [stem + ".ts", stem + ".tsx", stem + ".mts"]
        candidates += [base + e for e in JS_EXTENSIONS]
        candidates += [posixpath.join(base, "index" + e) for e in JS_EXTENSIONS]
        for candidate in candidates:
            if candidate in self.files:
                return candidate
        return None

    def _alias_targets(self, spec: str, importer: str) -> list[str]:
        targets: list[str] = []
        applicable = [c for c in self.configs if not c[0] or importer.startswith(c[0] + "/")]
        applicable.sort(key=lambda c: -len(c[0]))
        for _config_dir, base_dir, paths_map in applicable:
            for pattern, replacements in paths_map.items():
                if pattern.endswith("*") and spec.startswith(pattern[:-1]):
                    rest = spec[len(pattern) - 1:]
                    targets += [posixpath.join(base_dir, r.replace("*", rest)) for r in replacements]
                elif pattern == spec:
                    targets += [posixpath.join(base_dir, r) for r in replacements]
            targets.append(posixpath.join(base_dir, spec))  # baseUrl-relative import
        if spec.startswith(("@/", "~/")):
            # Common Vite / Next.js convention when no tsconfig alias exists.
            directory = posixpath.dirname(importer)
            while True:
                targets.append(posixpath.join(directory, "src", spec[2:]) if directory else f"src/{spec[2:]}")
                if not directory:
                    break
                directory = posixpath.dirname(directory)
        return [posixpath.normpath(t) for t in targets]

    def resolve(self, spec: str, importer: str) -> str | None:
        if spec.startswith("."):
            return self._probe(posixpath.normpath(posixpath.join(posixpath.dirname(importer), spec)))
        for target in self._alias_targets(spec, importer):
            found = self._probe(target)
            if found:
                return found
        return None

    @staticmethod
    def package_name(spec: str) -> str:
        if spec.startswith("node:"):
            return spec
        parts = spec.split("/")
        return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]


@dataclass
class _FileScope:
    names: dict[str, tuple[str, str]] = field(default_factory=dict)  # local -> (file, original name)
    modules: dict[str, str] = field(default_factory=dict)  # local alias -> file
    star_sources: list[str] = field(default_factory=list)
    imported_files: set[str] = field(default_factory=set)


class GraphBuilder:
    def __init__(self, files: dict[str, FileInfo], chunks: list[dict[str, Any]], root: Path | None = None):
        self.files = files
        self.locator = ChunkLocator(chunks)
        all_paths = set(files)
        self.python = PythonModules(all_paths)
        self.js = JsModules(all_paths, root)
        self.top_level: dict[str, dict[str, _Symbol]] = defaultdict(dict)
        self.methods: dict[str, dict[str, dict[str, _Symbol]]] = defaultdict(lambda: defaultdict(dict))
        self.by_name: dict[str, list[_Symbol]] = defaultdict(list)
        self.file_symbols: dict[str, list[_Symbol]] = defaultdict(list)
        for path, info in files.items():
            for raw in info.symbols:
                sym = _Symbol(file=path, name=raw["name"], qualified_name=raw["qualified_name"], kind=raw["kind"],
                              start=raw["start_line"], end=raw["end_line"], parent=raw.get("parent"),
                              chunk_id=raw.get("chunk_id"))
                self.file_symbols[path].append(sym)
                self.by_name[sym.name].append(sym)
                if sym.parent:
                    self.methods[path][sym.parent][sym.name] = sym
                else:
                    self.top_level[path][sym.name] = sym
        self.scopes: dict[str, _FileScope] = {}

    # ----- imports -------------------------------------------------------------------------
    def _resolve_module(self, info: FileInfo, ref: dict[str, Any]) -> str | None:
        if info.grammar == "python":
            return self.python.resolve(ref["module"], info.path, ref.get("level", 0))
        return self.js.resolve(ref["module"], info.path)

    def _external_name(self, info: FileInfo, ref: dict[str, Any]) -> str | None:
        if info.grammar == "python":
            if ref.get("level", 0) > 0 or not ref["module"]:
                return None
            return ref["module"].split(".")[0]
        if ref["module"].startswith("."):
            return None
        return self.js.package_name(ref["module"])

    def _build_scope(self, info: FileInfo, edges: list[Edge]) -> None:
        scope = _FileScope()
        for ref in info.imports:
            target = self._resolve_module(info, ref)
            names = ref.get("names") or []
            if info.grammar == "python":
                # `from pkg import sub` may import a submodule rather than a name.
                for original, local in names:
                    if original == "*":
                        continue
                    sub = self.python.resolve(".".join(p for p in [ref["module"], original] if p),
                                              info.path, ref.get("level", 0))
                    if sub:
                        scope.modules[local] = sub
                        scope.imported_files.add(sub)
                        edges.append(Edge(kind="import", src_file=info.path, dst_file=sub, line=ref["line"]))
                    elif target:
                        scope.names[local] = (target, original)
            elif target:
                for original, local in names:
                    if original != "*":
                        scope.names[local] = (target, original)
            if target is None:
                if not any(local in scope.modules for _, local in names):
                    external = self._external_name(info, ref)
                    if external:
                        edges.append(Edge(kind="import", src_file=info.path, external=external, line=ref["line"]))
                continue
            scope.imported_files.add(target)
            if ref.get("namespace"):
                scope.modules[ref["namespace"]] = target
            if any(original == "*" for original, _ in names):
                scope.star_sources.append(target)
            edges.append(Edge(kind="import", src_file=info.path, dst_file=target, line=ref["line"]))
            for original, _local in names:
                if original == "*":
                    continue
                sym = self.lookup(target, original)
                if sym is not None:
                    edges.append(Edge(kind="import", src_file=info.path, dst_file=sym.file,
                                      dst_symbol=sym.qualified_name, dst_chunk_id=sym.chunk_id, line=ref["line"]))
        self.scopes[info.path] = scope

    def lookup(self, file: str, name: str, depth: int = 0,
               seen: set[tuple[str, str]] | None = None) -> _Symbol | None:
        """Find a top-level symbol exported by ``file``, following re-exports and imports."""
        if seen is None:
            seen = set()
        if (file, name) in seen or depth > 4:
            return None
        seen.add((file, name))
        own = self.top_level.get(file, {})
        if name in own:
            return own[name]
        info = self.files.get(file)
        if name == "default" and info is not None and info.default_export:
            if info.default_export in own:
                return own[info.default_export]
            name = info.default_export
        scope = self.scopes.get(file)
        if scope is None and info is not None:
            scope = self._quick_scope(info)
        if scope is None:
            return None
        if name in scope.names:
            target, original = scope.names[name]
            return self.lookup(target, original, depth + 1, seen)
        for source in scope.star_sources:
            found = self.lookup(source, name, depth + 1, seen)
            if found:
                return found
        return None

    def _quick_scope(self, info: FileInfo) -> _FileScope:
        """Scope without edge creation, used while scopes are still being built."""
        scope = _FileScope()
        for ref in info.imports:
            target = self._resolve_module(info, ref)
            if not target:
                continue
            for original, local in ref.get("names") or []:
                if original == "*":
                    scope.star_sources.append(target)
                else:
                    scope.names[local] = (target, original)
        return scope

    # ----- calls ---------------------------------------------------------------------------
    def _enclosing(self, file: str, line: int) -> _Symbol | None:
        best = None
        for sym in self.file_symbols.get(file, []):
            if sym.start <= line <= sym.end and (best is None or sym.end - sym.start < best.end - best.start):
                best = sym
        return best

    def _class_named(self, file: str, name: str) -> tuple[str, str] | None:
        """(file, class name) for a class visible in ``file`` under ``name``."""
        own = self.top_level.get(file, {}).get(name)
        if own is not None and own.kind == "class":
            return own.file, own.name
        scope = self.scopes.get(file)
        if scope and name in scope.names:
            sym = self.lookup(*scope.names[name])
            if sym is not None and sym.kind == "class":
                return sym.file, sym.name
        return None

    def _global(self, name: str, file: str, methods: bool) -> list[tuple[_Symbol, float]]:
        if name in COMMON_NAMES or len(name) < 3:
            return []
        candidates = [s for s in self.by_name.get(name, []) if (s.parent is not None) == methods]
        if len(candidates) == 1:
            return [(candidates[0], 0.5)]
        imported = self.scopes.get(file, _FileScope()).imported_files
        preferred = [s for s in candidates if s.file in imported]
        if len(preferred) == 1:
            return [(preferred[0], 0.7)]
        return []

    def resolve_call(self, file: str, call: dict[str, Any], caller: _Symbol | None) -> list[tuple[_Symbol, float]]:
        name, receiver = call["name"], call.get("receiver")
        scope = self.scopes.get(file, _FileScope())
        if not receiver:
            own = self.top_level.get(file, {}).get(name)
            if own is not None:
                return [(own, 1.0)]
            if name in scope.names:
                sym = self.lookup(*scope.names[name])
                if sym is not None:
                    return [(sym, 0.95)]
            return self._global(name, file, methods=False)
        if receiver in ("self", "this", "cls") and caller is not None:
            class_name = caller.parent or (caller.name if caller.kind == "class" else None)
            if class_name:
                method = self.methods.get(file, {}).get(class_name, {}).get(name)
                if method is not None:
                    return [(method, 1.0)]
            return self._global(name, file, methods=True)
        if receiver in scope.modules:
            sym = self.lookup(scope.modules[receiver], name)
            return [(sym, 0.95)] if sym is not None else []
        if receiver.isidentifier():
            cls = self._class_named(file, receiver)
            if cls is not None:
                method = self.methods.get(cls[0], {}).get(cls[1], {}).get(name)
                if method is not None:
                    return [(method, 0.9)]
        return self._global(name, file, methods=True)

    # ----- build -----------------------------------------------------------------------------
    def build(self) -> list[Edge]:
        edges: list[Edge] = []
        for info in self.files.values():
            if info.grammar:
                self._build_scope(info, edges)
        best: dict[tuple, Edge] = {}  # one call edge per (caller chunk, callee chunk): the most confident
        for info in self.files.values():
            for call in info.calls:
                caller = self._enclosing(info.path, call["line"])
                src_chunk = self.locator.chunk_at(info.path, call["line"])
                for target, confidence in self.resolve_call(info.path, call, caller):
                    if target.chunk_id is None or target.chunk_id == src_chunk:
                        continue
                    key = (src_chunk, target.chunk_id)
                    if key in best and best[key].confidence >= confidence:
                        continue
                    best[key] = Edge(
                        kind="call", src_file=info.path,
                        src_symbol=caller.qualified_name if caller else None, src_chunk_id=src_chunk,
                        dst_file=target.file, dst_symbol=target.qualified_name, dst_chunk_id=target.chunk_id,
                        line=call["line"], confidence=confidence,
                    )
        edges.extend(best.values())
        unique: dict[tuple, Edge] = {}
        for edge in edges:
            key = (edge.kind, edge.src_file, edge.src_chunk_id, edge.dst_file, edge.dst_symbol,
                   edge.dst_chunk_id, edge.external)
            unique.setdefault(key, edge)
        return list(unique.values())
