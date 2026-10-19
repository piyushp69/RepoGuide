"""Extract definitions, imports and calls from a Python syntax tree."""

from __future__ import annotations

from tree_sitter import Node

from . import treesitter as ts
from .models import CallRef, Definition, Extraction, ImportRef, TopItem

_DOC_LIMIT = 400


def _docstring(definition: Node) -> str | None:
    body = definition.child_by_field_name("body")
    if body is None or not body.named_children:
        return None
    first = body.named_children[0]
    if first.type != "expression_statement" or not first.named_children:
        return None
    string = first.named_children[0]
    if string.type != "string":
        return None
    content = "".join(ts.text(c) for c in string.named_children if c.type == "string_content")
    content = " ".join(content.split())
    return content[:_DOC_LIMIT] or None


def _unwrap(node: Node) -> Node | None:
    if node.type == "decorated_definition":
        return node.child_by_field_name("definition")
    return node


def _definition(node: Node, outer: Node) -> Definition | None:
    name = ts.text(node.child_by_field_name("name"))
    if not name:
        return None
    start = ts.leading_start(outer)
    if node.type == "function_definition":
        return Definition(name=name, kind="function", start=start, def_start=ts.start_line(outer),
                          end=ts.end_line(outer), docstring=_docstring(node))
    members: list[Definition] = []
    body = node.child_by_field_name("body")
    if body is not None:
        for child in body.named_children:
            inner = _unwrap(child)
            if inner is None or inner.type != "function_definition":
                continue
            method_name = ts.text(inner.child_by_field_name("name"))
            if not method_name:
                continue
            members.append(Definition(
                name=method_name, kind="method", start=ts.leading_start(child),
                def_start=ts.start_line(child), end=ts.end_line(child), docstring=_docstring(inner),
            ))
    return Definition(name=name, kind="class", start=start, def_start=ts.start_line(outer),
                      end=ts.end_line(outer), docstring=_docstring(node), members=members)


def _imports(nodes: list[Node]) -> list[ImportRef]:
    refs: list[ImportRef] = []
    for node in nodes:
        line = ts.start_line(node)
        if node.type == "import_statement":
            for child in node.children_by_field_name("name"):
                if child.type == "aliased_import":
                    module = ts.text(child.child_by_field_name("name"))
                    alias = ts.text(child.child_by_field_name("alias")) or None
                else:
                    module, alias = ts.text(child), None
                if module:
                    # "import a.b" binds "a"; "import a.b as c" binds "c".
                    refs.append(ImportRef(module=module, line=line, namespace=alias or module.split(".")[0]))
        elif node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            level, module = 0, ""
            if module_node is not None and module_node.type == "relative_import":
                for child in module_node.children:
                    if child.type == "import_prefix":
                        level = ts.text(child).count(".")
                    elif child.type == "dotted_name":
                        module = ts.text(child)
            else:
                module = ts.text(module_node)
            names: list[list[str]] = []
            for child in node.children_by_field_name("name"):
                if child.type == "aliased_import":
                    original = ts.text(child.child_by_field_name("name"))
                    names.append([original, ts.text(child.child_by_field_name("alias")) or original])
                else:
                    value = ts.text(child)
                    names.append([value, value])
            if any(c.type == "wildcard_import" for c in node.children):
                names = [["*", "*"]]
            refs.append(ImportRef(module=module, line=line, level=level, names=names))
    return refs


def _calls(nodes: list[Node]) -> list[CallRef]:
    calls: list[CallRef] = []
    for node in nodes:
        function = node.child_by_field_name("function")
        if function is None:
            continue
        if function.type == "identifier":
            calls.append(CallRef(name=ts.text(function), receiver=None, line=ts.start_line(node)))
        elif function.type == "attribute":
            attr = ts.text(function.child_by_field_name("attribute"))
            receiver = ts.text(function.child_by_field_name("object"))
            if attr:
                calls.append(CallRef(name=attr, receiver=receiver[:80] or None, line=ts.start_line(node)))
    return calls


def _is_main_guard(node: Node) -> bool:
    if node.type != "if_statement":
        return False
    condition = ts.text(node.child_by_field_name("condition"))
    return "__name__" in condition and "__main__" in condition


def extract(root: Node) -> Extraction:
    items: list[TopItem] = []
    has_main_guard = False
    for node in root.named_children:
        if node.type == "comment":
            items.append(TopItem(ts.start_line(node), ts.end_line(node), is_comment=True))
            continue
        inner = _unwrap(node)
        definition = None
        if inner is not None and inner.type in ("function_definition", "class_definition"):
            definition = _definition(inner, node)
        has_main_guard = has_main_guard or _is_main_guard(node)
        items.append(TopItem(ts.start_line(node), ts.end_line(node), definition=definition))

    found = ts.captures("python", root)
    return Extraction(
        items=items,
        imports=_imports(found.get("import", [])),
        calls=_calls(found.get("call", [])),
        has_main_guard=has_main_guard,
    )
