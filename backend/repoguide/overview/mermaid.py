"""Deterministic Mermaid architecture diagram built from the module-level import graph."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

_UNSAFE = re.compile(r"[\"<>`{}\[\]|#;]")


def _label(text: str, limit: int = 48) -> str:
    text = _UNSAFE.sub("", " ".join(text.split()))
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def build_mermaid(modules: list[dict[str, Any]], descriptions: dict[str, str], entry_files: list[str],
                  tech_stack: list[dict[str, Any]], external_usage: dict[str, set[str]],
                  http_links: list[tuple[str, str]], max_modules: int = 24) -> str:
    """Modules become nodes grouped by top-level folder; edges are imports between modules."""
    if not modules:
        return ""  # nothing to draw; a node-less flowchart would render as an empty box
    ranked = sorted(modules, key=lambda m: (-(len(m["used_by"]) + len(m["depends_on"])), -m["lines"]))
    chosen = {m["path"] for m in ranked[:max_modules]}
    node_id = {path: f"m{i}" for i, path in enumerate(sorted(chosen))}
    entry_modules = {m["path"] for m in modules for f in m["files"] if f["path"] in set(entry_files)}

    lines = ["flowchart LR"]
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for module in modules:
        if module["path"] in chosen:
            top = module["path"].split("/")[0]
            groups[top].append(module)
    use_subgraphs = len(groups) > 1
    for group_index, (top, members) in enumerate(sorted(groups.items())):
        indent = "    " if use_subgraphs else "  "
        if use_subgraphs:
            lines.append(f'  subgraph g{group_index}["{_label(top, 30)}"]')
        for module in sorted(members, key=lambda m: m["path"]):
            count = len(module["files"])
            description = descriptions.get(module["path"]) or f"{count} file{'s' if count != 1 else ''}"
            label = f"{_label(module['path'], 40)}<br/>{_label(description)}"
            lines.append(f'{indent}{node_id[module["path"]]}["{label}"]')
        if use_subgraphs:
            lines.append("  end")

    for module in modules:
        if module["path"] not in chosen:
            continue
        for target in module["depends_on"]:
            if target in chosen:
                lines.append(f"  {node_id[module['path']]} --> {node_id[target]}")
    for source, target in http_links:
        if source in chosen and target in chosen:
            lines.append(f"  {node_id[source]} -. HTTP .-> {node_id[target]}")

    # The most widely used external frameworks, linked from the modules that import them.
    frameworks = [t for t in tech_stack if t["category"] in
                  ("Web framework", "Frontend framework", "Database", "Data / ML") and t["name"] in external_usage]
    for index, tech in enumerate(frameworks[:5]):
        ext_id = f"x{index}"
        lines.append(f'  {ext_id}{{{{"{_label(tech["name"], 30)}"}}}}')
        for module_path in sorted(external_usage[tech["name"]]):
            if module_path in chosen:
                lines.append(f"  {node_id[module_path]} -.-> {ext_id}")
        lines.append(f"  class {ext_id} external")

    lines.append("  classDef entry fill:#dcfce7,stroke:#16a34a,stroke-width:2px,color:#14532d")
    lines.append("  classDef external fill:#f1f5f9,stroke:#94a3b8,color:#334155,stroke-dasharray:3 3")
    entries = [node_id[p] for p in sorted(entry_modules) if p in node_id]
    if entries:
        lines.append(f"  class {','.join(entries)} entry")
    return "\n".join(lines)
