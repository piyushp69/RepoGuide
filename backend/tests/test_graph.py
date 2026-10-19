"""Import graph and best-effort call graph."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from repoguide.graph.resolver import ChunkLocator, FileInfo, GraphBuilder, JsModules, PythonModules
from repoguide.parsing import parse_file


def build(files: dict[str, str], root: Path | None = None):
    infos, chunks = {}, []
    for path, text in files.items():
        language = "python" if path.endswith(".py") else "typescript" if path.endswith((".ts", ".tsx")) else "javascript"
        parsed = parse_file(path, text, language)
        file_chunks = [{"id": f"{path}#{c.start_line}", "file_path": path, "start_line": c.start_line,
                        "end_line": c.end_line} for c in parsed.chunks]
        chunks += file_chunks
        locator = ChunkLocator(file_chunks)
        symbols = []
        for s in parsed.symbols:
            data = asdict(s)
            data["chunk_id"] = locator.chunk_at(path, s.start_line)
            symbols.append(data)
        infos[path] = FileInfo(path=path, language=language, grammar=parsed.grammar, symbols=symbols,
                               imports=[asdict(i) for i in parsed.imports], calls=[asdict(c) for c in parsed.calls],
                               default_export=parsed.default_export)
    return GraphBuilder(infos, chunks, root).build()


def call_pairs(edges):
    return {(e.src_symbol, e.dst_symbol) for e in edges if e.kind == "call"}


def import_pairs(edges):
    return {(e.src_file, e.dst_file or f"external:{e.external}") for e in edges if e.kind == "import" and not e.dst_symbol}


def test_python_module_resolution():
    modules = PythonModules(["app/__init__.py", "app/models.py", "app/services/orders.py", "scripts/json.py",
                             "src/pkg/__init__.py", "src/pkg/util.py"])
    assert modules.resolve("app.models", "app/services/orders.py") == "app/models.py"
    assert modules.resolve("models", "app/services/orders.py", level=2) == "app/models.py"
    assert modules.resolve("", "app/services/orders.py", level=2) == "app/__init__.py"
    assert modules.resolve("pkg.util", "tests/test_x.py") == "src/pkg/util.py"  # src layout
    assert modules.resolve("json", "app/models.py") is None  # stdlib is not shadowed by an unrelated file
    assert modules.resolve("json", "scripts/run.py") == "scripts/json.py"  # ...unless it is a sibling


def test_js_module_resolution(tmp_path: Path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "tsconfig.json").write_text(
        '{ // comment\n "compilerOptions": { "baseUrl": ".", "paths": { "@lib/*": ["src/lib/*"] }, }, }')
    files = ["web/tsconfig.json", "web/src/lib/util.ts", "web/src/components/index.tsx", "web/src/App.tsx",
             "web/src/esm.ts"]
    modules = JsModules(files, tmp_path)
    assert modules.resolve("./components", "web/src/App.tsx") == "web/src/components/index.tsx"
    assert modules.resolve("@lib/util", "web/src/App.tsx") == "web/src/lib/util.ts"
    assert modules.resolve("@/lib/util", "web/src/App.tsx") == "web/src/lib/util.ts"
    assert modules.resolve("./esm.js", "web/src/App.tsx") == "web/src/esm.ts"
    assert modules.resolve("react", "web/src/App.tsx") is None
    assert JsModules.package_name("@scope/pkg/deep") == "@scope/pkg"


def test_python_call_graph_across_files():
    edges = build({
        "shop/pricing.py": "def tax(x):\n    return x\n\ndef total(x):\n    return tax(x) + 1\n",
        "shop/orders.py": (
            "from shop.pricing import total\nfrom shop import pricing as p\n\n"
            "class Orders:\n    def place(self, x):\n        self.check(x)\n        return total(x)\n\n"
            "    def check(self, x):\n        return p.tax(x)\n"
        ),
        "main.py": "from shop.orders import Orders\n\nif __name__ == '__main__':\n    Orders().place(1)\n",
    })
    pairs = call_pairs(edges)
    assert ("total", "tax") in pairs
    assert ("Orders.place", "total") in pairs
    assert ("Orders.check", "tax") in pairs  # via module alias "p"
    assert (None, "Orders") in pairs  # module-level code instantiates the class
    assert ("shop/orders.py", "shop/pricing.py") in import_pairs(edges)
    assert all(e.confidence >= 0.9 for e in edges if e.kind == "call" and e.dst_symbol in ("tax", "total"))


def test_js_call_graph_with_default_and_named_imports():
    edges = build({
        "src/api.ts": "export function placeOrder(x: number) { return request(x); }\nfunction request(x: number) { return x; }\n",
        "src/Form.tsx": "import { placeOrder } from './api';\nexport default function Form() { placeOrder(1); return null; }\n",
        "src/App.tsx": "import Form from './Form';\nimport React from 'react';\nexport function App() { return <Form />; }\n",
    })
    pairs = call_pairs(edges)
    assert ("placeOrder", "request") in pairs
    assert ("Form", "placeOrder") in pairs
    assert ("App", "Form") in pairs  # JSX usage of a default import
    assert ("src/App.tsx", "external:react") in import_pairs(edges)


def test_sample_repo_graph(services, sample_project):
    edges = services.db.query("SELECT kind, src_symbol, dst_symbol, src_file, dst_file, external FROM edges "
                              "WHERE project_id = ?", (sample_project,))
    calls = {(e["src_symbol"], e["dst_symbol"]) for e in edges if e["kind"] == "call"}
    assert ("OrderService.create_order", "compute_order_total") in calls
    assert ("OrderService.create_order", "reserve_stock") in calls
    assert ("OrderService.cancel_order", "release_stock") in calls
    assert ("compute_order_total", "apply_discount") in calls
    assert ("OrderForm", "placeOrder") in calls
    externals = {e["external"] for e in edges if e["external"]}
    assert {"flask", "react", "itsdangerous"} <= externals
