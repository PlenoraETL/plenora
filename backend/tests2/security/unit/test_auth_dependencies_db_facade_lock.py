"""
Source-bound lock: `app.core.auth.dependencies` must NOT import
`app.db.db_service` or legacy config leaves.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_DEPENDENCIES_PY = (
    _BACKEND_ROOT / "app" / "core" / "auth" / "dependencies.py"
)


def test_dependencies_does_not_import_db_service():
    """`app/core/auth/dependencies.py` MUST NOT import the cross-engine
    DB facade. The canonical active-user lookup is
    `app.core.auth.db.get_user`.
    """
    text = _DEPENDENCIES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(_DEPENDENCIES_PY))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.db.db_service":
                    violations.append(
                        f"line {node.lineno}: "
                        f"`import app.db.db_service"
                        + (f" as {alias.asname}" if alias.asname else "")
                        + "`"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.module == "app.db.db_service":
                names = ", ".join(a.name for a in node.names)
                violations.append(
                    f"line {node.lineno}: "
                    f"`from app.db.db_service import {names}`"
                )
            if node.module == "app.db":
                for alias in node.names:
                    if alias.name == "db_service":
                        violations.append(
                            f"line {node.lineno}: "
                            f"`from app.db import db_service"
                            + (
                                f" as {alias.asname}"
                                if alias.asname
                                else ""
                            )
                            + "`"
                        )
    assert not violations, (
        "REGRESSIONE BE-GO-READY-AUTH-ENGINE-015: "
        "`app/core/auth/dependencies.py` re-introduce un import "
        "di `app.db.db_service`:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nIl canonical boundary per `get_user` e' "
        "`app.core.auth.db.get_user`."
    )


def test_dependencies_imports_auth_db_boundary():
    """Positive lock: dependencies must import `app.core.auth.db`."""
    text = _DEPENDENCIES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(_DEPENDENCIES_PY))
    found = False
    legacy_found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.core.auth.db":
                    found = True
                if alias.name == "app.db.config.users":
                    legacy_found = True
        elif isinstance(node, ast.ImportFrom):
            if node.module == "app.core.auth.db":
                found = True
            if node.module == "app.core.auth":
                found = found or any(alias.name == "db" for alias in node.names)
            if node.module == "app.db.config.users":
                legacy_found = True
            if node.module == "app.db.config":
                legacy_found = legacy_found or any(
                    alias.name == "users" for alias in node.names
                )
    assert found, (
        "`app/core/auth/dependencies.py` must import `app.core.auth.db` "
        "for the active-user DB lookup."
    )
    assert not legacy_found, (
        "`app/core/auth/dependencies.py` must not import "
        "`app.db.config.users`."
    )
