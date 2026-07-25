"""Node 1: deterministic AST parser.

Walks a repo's .py files and extracts the same structural JSON shape that
sample_data/sample_parsed_repo.json hand-wrote as a stand-in - so this is
literally the thing that mock was standing in for. No LLM calls anywhere in
this file; every fact extracted here comes from Python's own `ast` module
inspecting fixed language grammar (function defs, class defs, calls,
imports), never from guessing what the code "means".

Two kinds of facts are extracted, and they are not equally reliable:

  - Language-grammar facts (files, function/class names, imports, base
    classes) are 100% deterministic - the ast module can't get these wrong,
    because there's only one way a given piece of Python syntax parses.

  - Framework-pattern heuristics (is this class an ORM model? is this
    function an HTTP route? which model does this function mutate?) are
    pattern-matching against common SQLAlchemy/FastAPI/Flask conventions
    (a class inheriting from something named "Base"/"Model", a function
    decorated with @x.post(...), a variable assigned from a model
    constructor then passed to `.add(...)`). These will have false
    negatives on codebases that don't follow these conventions - that's
    inherent to inferring framework intent from syntax alone, not a bug to
    fix later.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

_ORM_BASE_NAMES = {"Base", "Model"}
_HTTP_DECORATOR_METHODS = {"get", "post", "put", "delete", "patch"}
_MUTATING_SESSION_METHODS = {"add", "delete"}
_KNOWN_EXTERNAL_SERVICES = {"stripe"}


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
    files = [_parse_file(rel_path, tree, model_class_names) for rel_path, tree in file_trees.items()]

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


def _parse_file(rel_path: str, tree: ast.Module, model_class_names: set[str]) -> dict:
    return {
        "path": rel_path,
        "language": "python",
        "imports": _extract_imports(tree),
        "classes": [_extract_class(node) for node in tree.body if isinstance(node, ast.ClassDef)],
        "functions": [
            _extract_function(node, model_class_names)
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
    if not isinstance(value_node, ast.Call):
        return None
    call_name = _dotted_name(value_node.func)
    if call_name is None:
        return None
    call_name = call_name.split(".")[-1]
    kwargs = {kw.arg: kw.value for kw in value_node.keywords}

    if call_name == "Column":
        field: dict = {"name": field_name, "type": _column_type(value_node)}
        if _is_constant_true(kwargs.get("primary_key")):
            field["primary_key"] = True
        if _is_constant_true(kwargs.get("unique")):
            field["unique"] = True
        if fk := _foreign_key_target(value_node):
            field["foreign_key"] = fk
        return field

    if call_name == "relationship":
        field = {"name": field_name, "type": "relationship"}
        if value_node.args and isinstance(value_node.args[0], ast.Constant):
            field["target"] = value_node.args[0].value
        if isinstance(kwargs.get("cascade"), ast.Constant):
            field["cascade"] = kwargs["cascade"].value
        return field

    return None


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


def _extract_function(node: ast.FunctionDef | ast.AsyncFunctionDef, model_class_names: set[str]) -> dict:
    http_method, route = _extract_route(node)
    result: dict = {"name": node.name, "calls": _extract_calls(node)}
    if http_method:
        result["http_method"] = http_method
    if route:
        result["route"] = route
    if mutates := _extract_mutations(node, model_class_names):
        result["mutates_models"] = mutates
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


def _extract_mutations(node: ast.FunctionDef | ast.AsyncFunctionDef, model_class_names: set[str]) -> list[str]:
    """Heuristic: track which local variables hold a model instance (via
    constructor call or a `.query(Model)...` chain), then flag a model as
    mutated when one of those variables is passed to `.add(...)` /
    `.delete(...)`, or when a model is instantiated at all (treated as a
    creation)."""
    var_models: dict[str, str] = {}
    mutated: set[str] = set()

    for sub in _walk_body(node):
        if not (isinstance(sub, ast.Assign) and len(sub.targets) == 1 and isinstance(sub.targets[0], ast.Name)):
            continue
        var_name = sub.targets[0].id
        value = sub.value
        if not isinstance(value, ast.Call):
            continue

        if query_call := _find_query_call(value):
            if query_call.args and (queried := _dotted_name(query_call.args[0])) in model_class_names:
                var_models[var_name] = queried
        elif (func_name := _dotted_name(value.func)) in model_class_names:
            var_models[var_name] = func_name
            mutated.add(func_name)

    for sub in _walk_body(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr in _MUTATING_SESSION_METHODS:
            for arg in sub.args:
                if isinstance(arg, ast.Name) and arg.id in var_models:
                    mutated.add(var_models[arg.id])

    return sorted(mutated)


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
