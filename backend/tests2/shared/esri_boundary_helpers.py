"""Shared helpers for ESRI/ArcGIS boundary contract tests."""

from __future__ import annotations

import ast
from pathlib import Path


BACKEND_DIR = Path(__file__).parents[2]
IMDF_DIR = BACKEND_DIR / "app" / "core" / "imdf"
SHARED_CONNECTIONS_DIR = BACKEND_DIR / "app" / "shared" / "connections"


def python_files_under(*roots: Path) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        files.extend(root.rglob("*.py"))
    return sorted(files)


def imports_in_file(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
    return modules


def imported_names_in_file(path: Path) -> list[tuple[str, str, int]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    imported_names: list[tuple[str, str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                imported_names.append((node.module, alias.name, node.lineno))
    return imported_names


def scan_arcgis_rest_mechanics(path: Path) -> list[tuple[int, str]]:
    patterns = (
        "._http",
        "_request_with_token",
        "/sharing/rest",
        "/rest/admin",
        "createFolder",
        "permanentDelete",
        "updateDefinition",
    )
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [(exc.lineno or 0, f"syntax error: {exc}")]

    violations: list[tuple[int, str]] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def is_dict_key(node: ast.AST) -> bool:
        parent = parents.get(node)
        return isinstance(parent, ast.Dict) and node in parent.keys

    def scan_node(node: ast.AST) -> None:
        if isinstance(node, ast.Attribute) and node.attr == "_http":
            violations.append((node.lineno, "._http"))
        elif isinstance(node, ast.Name) and node.id == "_request_with_token":
            violations.append((node.lineno, "_request_with_token"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            for pattern in patterns:
                if pattern == "._http":
                    continue
                if pattern in ("/sharing/rest", "/rest/admin") and pattern in node.value:
                    violations.append((node.lineno, pattern))
                elif pattern in {"createFolder", "permanentDelete", "updateDefinition"} and (
                    node.value == pattern
                    or f"/{pattern}" in node.value
                    or (is_dict_key(node) and pattern in node.value)
                ):
                    violations.append((node.lineno, pattern))

        for child in ast.iter_child_nodes(node):
            scan_node(child)

    def scan_body(body: list[ast.stmt]) -> None:
        statements = body
        if (
            statements
            and isinstance(statements[0], ast.Expr)
            and isinstance(statements[0].value, ast.Constant)
            and isinstance(statements[0].value.value, str)
        ):
            statements = statements[1:]
        for statement in statements:
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(statement, ast.ClassDef):
                    for part in [*statement.bases, *statement.keywords, *statement.decorator_list]:
                        scan_node(part)
                else:
                    parts: list[ast.AST] = [statement.args, *statement.decorator_list]
                    if statement.returns is not None:
                        parts.append(statement.returns)
                    for part in parts:
                        scan_node(part)
                scan_body(statement.body)
            else:
                scan_node(statement)

    scan_body(tree.body)
    return violations
