"""
Source-bound lock: auth user lookup paths must use config DB leaves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_AUTH_FILES = {
    "app/core/auth/auth.py": "app.db.config.users",
    "app/core/auth/ownership.py": "app.db.config.users",
    "app/core/auth/users_router.py": "app.db.config.users",
}


def _tree(rel_path: str) -> ast.AST:
    path = _BACKEND_ROOT / rel_path
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _imports_module(tree: ast.AST, module: str, leaf_name: str | None = None) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == module for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == module:
                return True
            if leaf_name and node.module == module.rpartition(".")[0]:
                if any(alias.name == leaf_name for alias in node.names):
                    return True
    return False


@pytest.mark.parametrize("rel_path", sorted(_AUTH_FILES))
def test_auth_user_lookup_paths_do_not_import_db_service(rel_path: str) -> None:
    tree = _tree(rel_path)
    assert not _imports_module(tree, "app.db.db_service", "db_service"), (
        f"{rel_path} re-introduced app.db.db_service. User/auth lookup "
        "paths must import app.db.config.users directly."
    )


@pytest.mark.parametrize("rel_path, leaf", sorted(_AUTH_FILES.items()))
def test_auth_user_lookup_paths_import_users_leaf(rel_path: str, leaf: str) -> None:
    tree = _tree(rel_path)
    assert _imports_module(tree, leaf, "users"), (
        f"{rel_path} no longer imports {leaf}. Update this guard only if "
        "the user lookup moved to a narrower canonical leaf."
    )

