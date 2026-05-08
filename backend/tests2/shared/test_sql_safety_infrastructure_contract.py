from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_SQL_SAFETY_DOTTED = "app.db.sql_safety"
_LEGACY_SAFE_SET_DOTTED = "app.db.config.safe_set"


def _imports_module(tree: ast.AST, dotted: str) -> bool:
    parent, _, leaf = dotted.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == dotted for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == dotted:
                return True
            if node.module == parent:
                if any(alias.name == leaf for alias in node.names):
                    return True
    return False


def test_safe_set_clause_is_owned_by_generic_db_infrastructure():
    import app.db.sql_safety as sql_safety

    assert sql_safety.safe_set_clause("users", {"email": "a@example.test"}) == (
        "email = %s",
        ("a@example.test",),
    )
    assert importlib.util.find_spec(_LEGACY_SAFE_SET_DOTTED) is None


def test_production_safe_set_users_do_not_import_config_safe_set():
    violations: list[str] = []
    for path in _APP.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        if _imports_module(tree, _LEGACY_SAFE_SET_DOTTED):
            violations.append(path.relative_to(_BACKEND_ROOT).as_posix())

    assert not violations, (
        "`safe_set_clause` is DB infrastructure and must be imported from "
        f"`{_SQL_SAFETY_DOTTED}`, not `{_LEGACY_SAFE_SET_DOTTED}`.\n"
        "Violations:\n  - " + "\n  - ".join(sorted(violations))
    )
