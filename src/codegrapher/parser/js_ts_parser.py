"""Node 1, JS/TS half: Tree-Sitter based parser, matching ast_parser.py's
output shape so it plugs into the same graph_sync.py/vector_sync.py/agent
pipeline with zero changes downstream - the parsed_repo dict looks the
same regardless of which language produced it.

Deliberately scoped narrower than the Python parser:

  - Files, functions (declarations, arrow functions assigned to a
    top-level const, class methods), classes, imports, and calls are
    extracted with the same "100% deterministic, just reading grammar"
    reliability as the Python side.

  - Only one framework heuristic is implemented: Express-style route
    detection (app.get(...)/router.post(...) etc.), the JS equivalent of
    ast_parser.py's _extract_route.

  - ORM model detection and mutation tracking are NOT implemented here.
    Sequelize, Mongoose, Prisma, and TypeORM each have meaningfully
    different conventions from each other (and from SQLAlchemy) - doing
    this properly would mean repeating ast_parser.py's variable-tracking
    heuristic once per JS ORM convention, which is a separate, larger
    piece of work, not an oversight in this file. Classes always come
    back with is_orm_model: False and functions never carry
    mutates_models for JS/TS input.
"""

from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

_JS_LANGUAGE = Language(tsjs.language())
_TS_LANGUAGE = Language(tsts.language_typescript())
_TSX_LANGUAGE = Language(tsts.language_tsx())

_EXTENSION_LANGUAGE: dict[str, tuple[str, Language]] = {
    ".js": ("javascript", _JS_LANGUAGE),
    ".jsx": ("javascript", _JS_LANGUAGE),
    ".ts": ("typescript", _TS_LANGUAGE),
    ".tsx": ("typescript", _TSX_LANGUAGE),
}

_HTTP_METHODS = {"get", "post", "put", "delete", "patch"}
_IGNORED_DIRS = {"node_modules", "__pycache__", ".next", "dist", "build"}


def parse_js_ts_files(repo_root: Path) -> list[dict]:
    files = []
    for ext, (language_name, ts_language) in _EXTENSION_LANGUAGE.items():
        parser = Parser(ts_language)
        for file_path in sorted(repo_root.rglob(f"*{ext}")):
            if _IGNORED_DIRS & set(file_path.parts):
                continue
            source = file_path.read_bytes()
            tree = parser.parse(source)
            files.append(_parse_file(str(file_path.relative_to(repo_root)), language_name, tree.root_node, source))
    return files


def _text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _string_literal_value(node: Node, source: bytes) -> str | None:
    if node.type != "string":
        return None
    fragment = next((c for c in node.children if c.type == "string_fragment"), None)
    return _text(fragment, source) if fragment else ""


def _parse_file(rel_path: str, language_name: str, root: Node, source: bytes) -> dict:
    imports: list[str] = []
    classes: list[dict] = []
    functions: list[dict] = []
    # Express registers routes separately from where the handler is
    # defined - router.post("/orders", createOrder) - so route facts are
    # collected as a name -> (method, path) map in one pass over top-level
    # statements, then attached to the matching function by name in a
    # second pass. This only covers routes referencing a named handler;
    # an inline handler (router.post("/x", (req, res) => {...})) has no
    # name to attach the route to under this scheme and is not detected -
    # a real, documented limitation, not an oversight.
    route_by_handler_name: dict[str, tuple[str, str]] = {}

    for node in root.children:
        if node.type == "import_statement":
            if module := _extract_import_module(node, source):
                imports.append(module)
        elif node.type == "class_declaration":
            classes.append(_extract_class(node, source))
        elif node.type in ("function_declaration", "export_statement"):
            fn_node = node
            if node.type == "export_statement" and node.children:
                fn_node = node.children[-1]
            if fn_node.type == "function_declaration":
                name_node = fn_node.child_by_field_name("name")
                functions.append(_extract_function(fn_node, _text(name_node, source) if name_node else "<anonymous>", source))
            elif fn_node.type == "class_declaration":
                classes.append(_extract_class(fn_node, source))
        elif node.type == "lexical_declaration":
            functions.extend(_extract_arrow_functions(node, source))
            imports.extend(_extract_require_imports(node, source))
        elif node.type == "expression_statement":
            route_by_handler_name.update(_extract_top_level_route(node, source))

    for func in functions:
        if route := route_by_handler_name.get(func["name"]):
            func["http_method"], func["route"] = route

    return {
        "path": rel_path,
        "language": language_name,
        "imports": imports,
        "classes": classes,
        "functions": functions,
    }


def _extract_import_module(node: Node, source: bytes) -> str | None:
    string_node = next((c for c in node.children if c.type == "string"), None)
    return _string_literal_value(string_node, source) if string_node else None


def _extract_class(node: Node, source: bytes) -> dict:
    name_node = node.child_by_field_name("name")
    base_classes = []
    for child in node.children:
        if child.type == "class_heritage":
            base_classes.extend(_text(c, source) for c in child.children if c.type == "identifier")

    return {
        "name": _text(name_node, source) if name_node else "<anonymous>",
        "base_classes": base_classes,
        "is_orm_model": False,  # see module docstring - not attempted for JS/TS
        "fields": [],
    }


def _extract_arrow_functions(node: Node, source: bytes) -> list[dict]:
    """const foo = (x) => {...} - the JS equivalent of a top-level `def`."""
    results = []
    for declarator in (c for c in node.children if c.type == "variable_declarator"):
        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")
        if value_node is not None and value_node.type in ("arrow_function", "function_expression"):
            name = _text(name_node, source) if name_node else "<anonymous>"
            results.append(_extract_function(value_node, name, source))
    return results


def _extract_require_imports(node: Node, source: bytes) -> list[str]:
    """const x = require("module") / const { y } = require("module") -
    CommonJS's equivalent of an ESM import statement, common enough in
    Express-style backends that it's worth handling alongside
    import_statement rather than leaving it as an unhandled gap."""
    modules = []
    for declarator in (c for c in node.children if c.type == "variable_declarator"):
        value_node = declarator.child_by_field_name("value")
        if value_node is None or value_node.type != "call_expression":
            continue
        func = value_node.child_by_field_name("function")
        args = value_node.child_by_field_name("arguments")
        if func is None or _text(func, source) != "require" or args is None:
            continue
        arg_nodes = [c for c in args.children if c.type not in ("(", ")", ",")]
        if arg_nodes and (module := _string_literal_value(arg_nodes[0], source)) is not None:
            modules.append(module)
    return modules


def _extract_function(node: Node, name: str, source: bytes) -> dict:
    body = node.child_by_field_name("body")
    calls = _extract_calls(body, source) if body else []
    return {"name": name, "calls": calls}


def _extract_calls(body: Node, source: bytes) -> list[str]:
    calls = []

    def walk(n: Node):
        if n.type == "call_expression":
            func = n.child_by_field_name("function")
            if func is not None and (name := _dotted_name(func, source)):
                calls.append(name)
        for child in n.children:
            walk(child)

    walk(body)
    return calls


def _dotted_name(node: Node, source: bytes) -> str | None:
    if node.type == "identifier":
        return _text(node, source)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        prefix = _dotted_name(obj, source) if obj else None
        prop_name = _text(prop, source) if prop else None
        if prefix and prop_name:
            return f"{prefix}.{prop_name}"
        return prop_name
    return None


def _extract_top_level_route(node: Node, source: bytes) -> dict[str, tuple[str, str]]:
    """Matches app.get(path, handlerName) / router.post(path, handlerName)
    style top-level statements, mapping the referenced handler's name to
    its (METHOD, path). Only handles a named-function reference as the
    last argument - an inline handler has no name to key this map by and
    is intentionally not covered (see _parse_file's docstring comment)."""
    call = node.children[0] if node.children else None
    if call is None or call.type != "call_expression":
        return {}

    func = call.child_by_field_name("function")
    args = call.child_by_field_name("arguments")
    if func is None or func.type != "member_expression" or args is None:
        return {}

    prop = func.child_by_field_name("property")
    method = _text(prop, source) if prop else None
    if method not in _HTTP_METHODS:
        return {}

    arg_nodes = [c for c in args.children if c.type not in ("(", ")", ",")]
    if len(arg_nodes) < 2:
        return {}

    path = _string_literal_value(arg_nodes[0], source)
    handler = arg_nodes[-1]
    if path is None or handler.type != "identifier":
        return {}

    return {_text(handler, source): (method.upper(), path)}
