"""Extract definitions, imports and calls from JavaScript / TypeScript syntax trees."""

from __future__ import annotations

from tree_sitter import Node

from . import treesitter as ts
from .models import CallRef, Definition, Extraction, ImportRef, TopItem

FUNCTION_VALUES = {"arrow_function", "function_expression", "function", "generator_function"}
CLASS_DECLARATIONS = {"class_declaration", "abstract_class_declaration"}
_DOC_LIMIT = 400


def _doc(node: Node) -> str | None:
    doc = ts.preceding_comment(node)
    return doc[:_DOC_LIMIT] if doc else None


def _string_value(node: Node | None) -> str | None:
    if node is None or node.type != "string":
        return None
    return "".join(ts.text(c) for c in node.named_children if c.type == "string_fragment") or None


def _wraps_function(call: Node) -> bool:
    """True for higher-order wrappers such as React.memo(() => ...) or withAuth(async (req) => ...)."""
    args = call.child_by_field_name("arguments")
    return args is not None and any(a.type in FUNCTION_VALUES for a in args.named_children)


def _member_function_name(left: Node) -> str | None:
    """module.exports.foo -> foo, exports.foo -> foo, Foo.prototype.bar -> Foo.bar."""
    parts = ts.text(left).replace("?.", ".").split(".")
    if len(parts) < 2:
        return None
    if parts[0] in ("module", "exports"):
        tail = [p for p in parts[1:] if p != "exports"]
        return tail[-1] if tail else None
    if "prototype" in parts:
        idx = parts.index("prototype")
        return ".".join(parts[:idx] + parts[idx + 1:])
    return ".".join(parts[-2:])


def _class_members(class_node: Node) -> list[Definition]:
    members: list[Definition] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return members
    for member in body.named_children:
        name = None
        if member.type in ("method_definition", "abstract_method_signature", "method_signature"):
            name = ts.text(member.child_by_field_name("name"))
        elif member.type in ("field_definition", "public_field_definition"):
            value = member.child_by_field_name("value")
            if value is not None and value.type in FUNCTION_VALUES:
                name = ts.text(member.child_by_field_name("property") or member.child_by_field_name("name"))
        if not name:
            continue
        members.append(Definition(
            name=name, kind="method", start=ts.leading_start(member, ("decorator",)),
            def_start=ts.start_line(member), end=ts.end_line(member), docstring=_doc(member),
        ))
    return members


def _class_definition(node: Node, outer: Node, name: str | None = None) -> Definition | None:
    name = name or ts.text(node.child_by_field_name("name"))
    if not name:
        return None
    return Definition(name=name, kind="class", start=ts.leading_start(outer), def_start=ts.start_line(outer),
                      end=ts.end_line(outer), docstring=_doc(outer), members=_class_members(node))


def _simple(name: str, kind: str, outer: Node) -> Definition:
    return Definition(name=name, kind=kind, start=ts.leading_start(outer), def_start=ts.start_line(outer),
                      end=ts.end_line(outer), docstring=_doc(outer))


def _declaration_definitions(decl: Node, outer: Node) -> list[Definition]:
    kind = decl.type
    if kind in ("function_declaration", "generator_function_declaration", "function_signature"):
        name = ts.text(decl.child_by_field_name("name"))
        return [_simple(name, "function", outer)] if name else []
    if kind in CLASS_DECLARATIONS or kind == "class":
        definition = _class_definition(decl, outer)
        return [definition] if definition else []
    if kind in ("lexical_declaration", "variable_declaration"):
        found: list[Definition] = []
        for declarator in decl.named_children:
            if declarator.type != "variable_declarator":
                continue
            name_node = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if name_node is None or name_node.type != "identifier" or value is None:
                continue
            name = ts.text(name_node)
            if value.type in FUNCTION_VALUES or (value.type == "call_expression" and _wraps_function(value)):
                found.append(_simple(name, "function", outer))
            elif value.type == "class":
                definition = _class_definition(value, outer, name=name)
                if definition:
                    found.append(definition)
        return found
    if kind == "interface_declaration":
        name = ts.text(decl.child_by_field_name("name"))
        return [_simple(name, "interface", outer)] if name else []
    if kind == "type_alias_declaration":
        name = ts.text(decl.child_by_field_name("name"))
        return [_simple(name, "type", outer)] if name else []
    if kind == "enum_declaration":
        name = ts.text(decl.child_by_field_name("name"))
        return [_simple(name, "enum", outer)] if name else []
    if kind in ("internal_module", "module"):
        name = ts.text(decl.child_by_field_name("name"))
        return [_simple(name, "namespace", outer)] if name else []
    if kind == "ambient_declaration":
        for child in decl.named_children:
            found = _declaration_definitions(child, outer)
            if found:
                return found
        return []
    if kind == "expression_statement" and decl.named_children:
        child = decl.named_children[0]
        if child.type == "internal_module":
            return _declaration_definitions(child, outer)
        if child.type == "assignment_expression":
            left, right = child.child_by_field_name("left"), child.child_by_field_name("right")
            if left is not None and right is not None and left.type == "member_expression" \
                    and right.type in FUNCTION_VALUES:
                name = _member_function_name(left)
                if name:
                    return [_simple(name, "function", outer)]
    return []


def _top_level(node: Node) -> tuple[list[Definition], str | None]:
    """Definitions in a top-level statement and the default-export name it declares, if any."""
    if node.type != "export_statement":
        return _declaration_definitions(node, node), None
    is_default = any(c.type == "default" for c in node.children)
    declaration = node.child_by_field_name("declaration")
    if declaration is not None:
        found = _declaration_definitions(declaration, node)
        return found, (found[0].name if is_default and found else None)
    value = node.child_by_field_name("value")
    if value is None or not is_default:
        return [], None
    if value.type == "identifier":
        return [], ts.text(value)
    if value.type in FUNCTION_VALUES:
        name = ts.text(value.child_by_field_name("name")) or "default"
        return [_simple(name, "function", node)], name
    if value.type == "class":
        definition = _class_definition(value, node, name=ts.text(value.child_by_field_name("name")) or "default")
        return ([definition], definition.name) if definition else ([], None)
    return [], None


def _import_statement(node: Node) -> ImportRef | None:
    module = _string_value(node.child_by_field_name("source"))
    if not module:
        return None
    ref = ImportRef(module=module, line=ts.start_line(node))
    for child in node.named_children:
        if child.type != "import_clause":
            continue
        for part in child.named_children:
            if part.type == "identifier":
                ref.names.append(["default", ts.text(part)])
            elif part.type == "namespace_import":
                ids = [c for c in part.named_children if c.type == "identifier"]
                if ids:
                    ref.namespace = ts.text(ids[0])
            elif part.type == "named_imports":
                for spec in part.named_children:
                    if spec.type != "import_specifier":
                        continue
                    original = ts.text(spec.child_by_field_name("name"))
                    alias = ts.text(spec.child_by_field_name("alias")) or original
                    if original:
                        ref.names.append([original, alias])
    return ref


def _reexport(node: Node) -> ImportRef | None:
    module = _string_value(node.child_by_field_name("source"))
    if not module:
        return None
    ref = ImportRef(module=module, line=ts.start_line(node), reexport=True)
    for child in node.named_children:
        if child.type == "export_clause":
            for spec in child.named_children:
                if spec.type == "export_specifier":
                    original = ts.text(spec.child_by_field_name("name"))
                    alias = ts.text(spec.child_by_field_name("alias")) or original
                    if original:
                        ref.names.append([original, alias])
        elif child.type == "namespace_export":
            ids = [c for c in child.named_children if c.type == "identifier"]
            ref.names.append(["*", ts.text(ids[0]) if ids else "*"])
    if not ref.names:
        ref.names.append(["*", "*"])
    return ref


def _require_binding(call: Node, ref: ImportRef) -> None:
    """Fill names/namespace for `const x = require('m')` and `const {a, b} = require('m')`."""
    parent = call.parent
    if parent is None or parent.type != "variable_declarator":
        return
    target = parent.child_by_field_name("name")
    if target is None:
        return
    if target.type == "identifier":
        ref.namespace = ts.text(target)
    elif target.type == "object_pattern":
        for prop in target.named_children:
            if prop.type == "shorthand_property_identifier_pattern":
                ref.names.append([ts.text(prop), ts.text(prop)])
            elif prop.type == "pair_pattern":
                key = ts.text(prop.child_by_field_name("key"))
                value = ts.text(prop.child_by_field_name("value"))
                if key:
                    ref.names.append([key, value or key])


def _calls_and_dynamic_imports(found: dict[str, list[Node]]) -> tuple[list[CallRef], list[ImportRef]]:
    calls: list[CallRef] = []
    imports: list[ImportRef] = []
    for node in found.get("call", []):
        function = node.child_by_field_name("function")
        if function is None:
            continue
        line = ts.start_line(node)
        if function.type in ("identifier", "import"):
            name = ts.text(function)
            if name in ("require", "import"):
                args = node.child_by_field_name("arguments")
                first = args.named_children[0] if args is not None and args.named_children else None
                module = _string_value(first)
                if module:
                    ref = ImportRef(module=module, line=line, dynamic=(name == "import"))
                    _require_binding(node, ref)
                    imports.append(ref)
                continue
            calls.append(CallRef(name=name, receiver=None, line=line))
        elif function.type == "member_expression":
            prop = ts.text(function.child_by_field_name("property"))
            receiver = ts.text(function.child_by_field_name("object"))
            if prop:
                calls.append(CallRef(name=prop, receiver=receiver[:80] or None, line=line))
    for node in found.get("new", []):
        ctor = node.child_by_field_name("constructor")
        if ctor is None:
            continue
        if ctor.type == "identifier":
            calls.append(CallRef(name=ts.text(ctor), receiver=None, line=ts.start_line(node), kind="new"))
        elif ctor.type == "member_expression":
            calls.append(CallRef(name=ts.text(ctor.child_by_field_name("property")),
                                 receiver=ts.text(ctor.child_by_field_name("object"))[:80] or None,
                                 line=ts.start_line(node), kind="new"))
    for node in found.get("jsx", []):
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        if name_node.type == "identifier":
            name = ts.text(name_node)
            if name[:1].isupper():  # lower-case tags are HTML elements
                calls.append(CallRef(name=name, receiver=None, line=ts.start_line(node), kind="jsx"))
        elif name_node.type in ("member_expression", "nested_identifier"):
            parts = ts.text(name_node).split(".")
            calls.append(CallRef(name=parts[-1], receiver=".".join(parts[:-1]) or None,
                                 line=ts.start_line(node), kind="jsx"))
    return calls, imports


def extract(root: Node, grammar: str) -> Extraction:
    items: list[TopItem] = []
    default_export: str | None = None
    for node in root.named_children:
        if node.type == "comment":
            items.append(TopItem(ts.start_line(node), ts.end_line(node), is_comment=True))
            continue
        definitions, default_name = _top_level(node)
        default_export = default_name or default_export
        items.append(TopItem(
            ts.start_line(node), ts.end_line(node),
            definition=definitions[0] if definitions else None,
            extra_definitions=definitions[1:],
        ))

    found = ts.captures(grammar, root)
    imports: list[ImportRef] = []
    for node in found.get("import", []):
        ref = _import_statement(node)
        if ref:
            imports.append(ref)
    for node in found.get("export", []):
        if node.child_by_field_name("source") is not None:
            ref = _reexport(node)
            if ref:
                imports.append(ref)
    calls, dynamic_imports = _calls_and_dynamic_imports(found)
    imports.extend(dynamic_imports)
    return Extraction(items=items, imports=imports, calls=calls, default_export=default_export)
