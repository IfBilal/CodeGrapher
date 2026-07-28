"""Node 1: deterministic AST parser.

parse_repo() is the single entry point for the whole repo, regardless of
language - it handles .py files directly (this module) and delegates
.js/.jsx/.ts/.tsx files to js_ts_parser.py (a separate Tree-Sitter based
parser, since Python's own `ast` module only understands Python syntax),
merging both into one file list under one repo_name. See js_ts_parser.py's
own docstring for what's deliberately out of scope on the JS/TS side.

Walks a repo's .py files and extracts a structural JSON shape (files,
functions, classes, calls, imports). No LLM calls anywhere in
this file; every fact extracted here comes from Python's own `ast` module
inspecting fixed language grammar (function defs, class defs, calls,
imports), never from guessing what the code "means".

Two kinds of facts are extracted, and they are not equally reliable:

  - Language-grammar facts (files, function/class names, imports, base
    classes) are 100% deterministic - the ast module can't get these wrong,
    because there's only one way a given piece of Python syntax parses.

  - Framework-pattern heuristics (is this class an ORM model? is this
    function an HTTP route? which model does this function mutate?) are
    pattern-matching against common framework conventions (a class
    inheriting from a known ORM base class, a function decorated with
    @x.post(...), a variable assigned from a model constructor then passed
    to `.add(...)`, possibly through a same-file helper). These will have
    false negatives on codebases that don't follow a registered convention
    (see frameworks.py) or that route a mutation through several layers of
    indirection - that's inherent to inferring intent from syntax alone,
    not a bug to fix later. Where the heuristic finds a mutating call it
    can't confidently attribute to a known model, it records that
    explicitly (unresolved_mutation_calls) instead of staying silent.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

from codegrapher.parser.frameworks import ORM_PROFILES
from codegrapher.parser.js_ts_parser import parse_js_ts_files

_HTTP_DECORATOR_METHODS = {"get", "post", "put", "delete", "patch"}
_MUTATING_SESSION_METHODS = {"add", "delete"}
_KNOWN_EXTERNAL_SERVICES = {"stripe"}

_ORM_BASE_NAMES = {name for profile in ORM_PROFILES for name in profile.base_class_names}
_FIELD_CALL_NAMES = {name for profile in ORM_PROFILES for name in profile.field_call_names}
_RELATIONSHIP_CALL_NAMES = {name for profile in ORM_PROFILES for name in profile.relationship_call_names}


def parse_repo(repo_root: Path) -> dict:
    repo_root = Path(repo_root)
    file_trees: dict[str, ast.Module] = {}

    for py_file in sorted(repo_root.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            tree = ast.parse(py_file.read_text(), filename=str(py_file))
        except SyntaxError:
            continue
        file_trees[str(py_file.relative_to(repo_root))] = tree

    model_class_names = _collect_model_class_names(file_trees)
    mutating_params = _collect_mutating_param_positions(file_trees)
    files = [_parse_file(rel_path, tree, model_class_names, mutating_params) for rel_path, tree in file_trees.items()]

    # JS/TS files go through a separate Tree-Sitter based parser
    # (js_ts_parser.py) - different grammar, different library - but
    # produce the same file-dict shape, so they just get appended to the
    # same list with no further merging logic needed.
    files.extend(parse_js_ts_files(repo_root))

    return {"repo_name": repo_root.name, "files": files}


def _collect_model_class_names(file_trees: dict[str, ast.Module]) -> set[str]:
    return {
        node.name
        for tree in file_trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _is_orm_model(node)
    }


def _is_orm_model(class_node: ast.ClassDef) -> bool:
    return any(_dotted_name(base) and _dotted_name(base).split(".")[-1] in _ORM_BASE_NAMES for base in class_node.bases)


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return None


def _parse_file(
    rel_path: str,
    tree: ast.Module,
    model_class_names: set[str],
    mutating_params: dict[str, set[int]],
) -> dict:
    return {
        "path": rel_path,
        "language": "python",
        "imports": _extract_imports(tree),
        "classes": [_extract_class(node) for node in tree.body if isinstance(node, ast.ClassDef)],
        "functions": [
            _extract_function(node, model_class_names, mutating_params)
            for node in tree.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ],
    }


def _extract_imports(tree: ast.Module) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return imports


def _extract_class(node: ast.ClassDef) -> dict:
    fields = []
    for item in node.body:
        if isinstance(item, ast.Assign) and len(item.targets) == 1 and isinstance(item.targets[0], ast.Name):
            field = _extract_field(item.targets[0].id, item.value)
            if field:
                fields.append(field)

    return {
        "name": node.name,
        "base_classes": [name for base in node.bases if (name := _dotted_name(base))],
        "is_orm_model": _is_orm_model(node),
        "fields": fields,
    }


def _extract_field(field_name: str, value_node: ast.AST) -> dict | None:
    """Tries every registered ORM profile's field/relationship call names in
    turn. A field call's own name (e.g. "Column" vs "CharField") is what
    actually disambiguates which profile applies - see frameworks.py."""
    if not isinstance(value_node, ast.Call):
        return None
    call_name = _dotted_name(value_node.func)
    if call_name is None:
        return None
    call_name = call_name.split(".")[-1]
    kwargs = {kw.arg: kw.value for kw in value_node.keywords}

    if call_name == "Column":
        return _extract_sqlalchemy_column_field(field_name, value_node, kwargs)

    if call_name == "relationship":
        return _extract_relationship_field(field_name, value_node, kwargs)

    if call_name in _FIELD_CALL_NAMES:
        return _extract_django_style_field(field_name, call_name, value_node, kwargs)

    if call_name in _RELATIONSHIP_CALL_NAMES:
        return _extract_relationship_field(field_name, value_node, kwargs)

    return None


def _extract_sqlalchemy_column_field(field_name: str, value_node: ast.Call, kwargs: dict) -> dict:
    field: dict = {"name": field_name, "type": _column_type(value_node)}
    if _is_constant_true(kwargs.get("primary_key")):
        field["primary_key"] = True
    if _is_constant_true(kwargs.get("unique")):
        field["unique"] = True
    if fk := _foreign_key_target(value_node):
        field["foreign_key"] = fk
    return field


def _extract_django_style_field(field_name: str, call_name: str, value_node: ast.Call, kwargs: dict) -> dict:
    """Django-style fields: the call name itself is the type (CharField,
    IntegerField, ...), and ForeignKey's target model is its first
    positional argument rather than a nested ForeignKey(...) call."""
    field: dict = {"name": field_name, "type": call_name}
    if _is_constant_true(kwargs.get("primary_key")):
        field["primary_key"] = True
    if _is_constant_true(kwargs.get("unique")):
        field["unique"] = True
    if call_name in {"ForeignKey", "OneToOneField"} and value_node.args:
        if target := _dotted_name(value_node.args[0]):
            field["foreign_key"] = target
    return field


def _extract_relationship_field(field_name: str, value_node: ast.Call, kwargs: dict) -> dict:
    field: dict = {"name": field_name, "type": "relationship"}
    if value_node.args:
        if isinstance(value_node.args[0], ast.Constant):
            field["target"] = value_node.args[0].value
        elif target := _dotted_name(value_node.args[0]):
            field["target"] = target
    if isinstance(kwargs.get("cascade"), ast.Constant):
        field["cascade"] = kwargs["cascade"].value
    return field


def _column_type(call_node: ast.Call) -> str | None:
    return next((arg.id for arg in call_node.args if isinstance(arg, ast.Name)), None)


def _foreign_key_target(call_node: ast.Call) -> str | None:
    for arg in call_node.args:
        if isinstance(arg, ast.Call) and _dotted_name(arg.func) == "ForeignKey" and arg.args:
            if isinstance(arg.args[0], ast.Constant):
                return arg.args[0].value
    return None


def _is_constant_true(node: ast.AST | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _walk_body(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterator[ast.AST]:
    """Walk only the function's body, skipping its decorator expressions."""
    for stmt in node.body:
        yield from ast.walk(stmt)


def _collect_mutating_param_positions(file_trees: dict[str, ast.Module]) -> dict[str, set[int]]:
    """For every function defined anywhere in the repo, figure out which of
    its own positional parameters get passed straight to `.add(...)` /
    `.delete(...)` inside its body. This lets mutation-tracking follow a
    model instance through one level of same-file helper calls instead of
    losing the trail the moment it leaves the calling function - e.g.

        def save(order):
            db_session.add(order)

        def create_order(payload):
            order = Order(...)
            save(order)   # previously invisible; now resolved via this map

    Keyed by function name only (not fully qualified) - a real limitation
    if two unrelated functions share a name, but a reasonable trade-off
    given the parser has no import-resolution step to disambiguate further.
    """
    mutating_params: dict[str, set[int]] = {}
    for tree in file_trees.values():
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            param_names = [a.arg for a in node.args.args]
            positions = set()
            for sub in _walk_body(node):
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _MUTATING_SESSION_METHODS:
                    for arg in sub.args:
                        if isinstance(arg, ast.Name) and arg.id in param_names:
                            positions.add(param_names.index(arg.id))
            if positions:
                mutating_params.setdefault(node.name, set()).update(positions)
    return mutating_params


def _extract_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    model_class_names: set[str],
    mutating_params: dict[str, set[int]],
) -> dict:
    http_method, route = _extract_route(node)
    result: dict = {"name": node.name, "calls": _extract_calls(node)}
    if http_method:
        result["http_method"] = http_method
    if route:
        result["route"] = route
    mutates, unresolved = _extract_mutations(node, model_class_names, mutating_params)
    if mutates:
        result["mutates_models"] = mutates
    if unresolved:
        result["unresolved_mutation_calls"] = unresolved
    if external := _extract_external_dependency(node):
        result["external_dependency"] = external
    return result


def _extract_route(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[str | None, str | None]:
    for decorator in node.decorator_list:
        if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
            continue
        method_name = decorator.func.attr
        route = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) else None

        if method_name in _HTTP_DECORATOR_METHODS:
            return method_name.upper(), route

        if method_name == "route":
            methods_kw = next((kw for kw in decorator.keywords if kw.arg == "methods"), None)
            method = "GET"
            if methods_kw is not None and isinstance(methods_kw.value, ast.List) and methods_kw.value.elts:
                first = methods_kw.value.elts[0]
                if isinstance(first, ast.Constant):
                    method = first.value
            return method, route

    return None, None


def _extract_calls(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [name for sub in _walk_body(node) if isinstance(sub, ast.Call) and (name := _dotted_name(sub.func))]


def _extract_mutations(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    model_class_names: set[str],
    mutating_params: dict[str, set[int]],
) -> tuple[list[str], list[str]]:
    """Heuristic: track which local variables hold a model instance (via
    constructor call, a `.query(Model)...` chain, or a plain reassignment
    from another tracked variable), then flag a model as mutated when one
    of those variables is passed to `.add(...)` / `.delete(...)` directly,
    or to a same-file helper function known (via mutating_params) to mutate
    that parameter itself.

    Returns (mutated_model_names, unresolved_mutation_call_arg_names) - the
    second list surfaces `.add(...)`/`.delete(...)` calls whose argument
    could not be traced back to a known model, instead of silently
    dropping them as if nothing happened."""
    var_models: dict[str, str] = {}
    mutated: set[str] = set()
    unresolved: set[str] = set()

    for sub in _walk_body(node):
        if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name)):
            continue
        var_name = sub.targets[0].id
        value = sub.value

        if isinstance(value, ast.Name) and value.id in var_models:
            var_models[var_name] = var_models[value.id]
            continue

        if not isinstance(value, ast.Call):
            continue

        if query_call := _find_query_call(value):
            if query_call.args and (queried := _dotted_name(query_call.args[0])) in model_class_names:
                var_models[var_name] = queried
        elif (func_name := _dotted_name(value.func)) in model_class_names:
            var_models[var_name] = func_name
            mutated.add(func_name)

    for sub in _walk_body(node):
        if not isinstance(sub, ast.Call):
            continue

        if isinstance(sub.func, ast.Attribute) and sub.func.attr in _MUTATING_SESSION_METHODS:
            for arg in sub.args:
                if isinstance(arg, ast.Name) and arg.id in var_models:
                    mutated.add(var_models[arg.id])
                elif isinstance(arg, ast.Name):
                    unresolved.add(arg.id)

        elif isinstance(sub.func, ast.Name) and sub.func.id in mutating_params:
            for position in mutating_params[sub.func.id]:
                if position < len(sub.args) and isinstance(sub.args[position], ast.Name):
                    arg_name = sub.args[position].id
                    if arg_name in var_models:
                        mutated.add(var_models[arg_name])

    return sorted(mutated), sorted(unresolved)


def _find_query_call(call_node: ast.Call) -> ast.Call | None:
    """Walk a chained call like db_session.query(Order).get(id) back to the .query(...) link."""
    node: ast.AST = call_node
    while isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr == "query":
            return node
        if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Call):
            node = node.func.value
            continue
        return None
    return None


def _extract_external_dependency(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    for sub in _walk_body(node):
        if isinstance(sub, ast.Call) and (name := _dotted_name(sub.func)):
            root = name.split(".")[0]
            if root in _KNOWN_EXTERNAL_SERVICES:
                return root
    return None
