"""Source-bound lock — `app.core.auth.dependencies` must NOT
import `app.db.db_service`.

BE-GO-READY-AUTH-ENGINE-015-DEPENDENCIES-OFF-DB-SERVICE.

`app.core.auth.dependencies` is the canonical leaf for the five
FastAPI auth dependency callables (`get_current_user`,
`require_user_or_above`, `require_not_esecutore`,
`require_admin`, `require_superadmin`). Pre-015 it consumed
`app.db.db_service` (the cross-engine bulk re-export bundle)
via ``import app.db.db_service as db`` and called
``db.get_user(...)`` for the active-user check inside
``get_current_user``.

Post-015 the leaf imports the canonical
``app.db.config.users`` directly. The bulk re-export bundle is
no longer in dependencies.py's import graph; this test pins the
boundary so future edits cannot silently re-introduce the
facade dependency.

Source-bound (AST-based): we read the file as text and walk the
import nodes. No runtime side effect. Same posture as
``test_auth_facade_strangler_contract.py`` and
``test_resource_engine_anti_god_engine_lock.py``.
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
    """`app/core/auth/dependencies.py` MUST NOT import
    ``app.db.db_service`` (the cross-engine bulk re-export
    bundle). The canonical lookup site for the active-user
    check is ``app.db.config.users.get_user`` (canonical leaf
    used by every other consumer migrated under
    BE-GO-READY-DB-ENGINE-002 / -003 / -004 / -005).

    Three import shapes are forbidden:
      - ``import app.db.db_service [as <alias>]``
      - ``from app.db.db_service import <X>``
      - ``from app.db import db_service [as <alias>]``
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
        + "\nIl canonical leaf per `get_user` e' "
        "`app.db.config.users.get_user`. Il facade "
        "`app.db.db_service` re-esporta lo stesso oggetto ma "
        "drag l'intero cross-engine bundle (results +"
        " logs + config); il leaf rimuove quella superficie."
    )


def test_dependencies_imports_users_leaf():
    """Positive lock: `app/core/auth/dependencies.py` MUST
    import ``app.db.config.users`` (the canonical leaf the
    active-user check now consumes). If this assertion fails
    someone migrated AWAY from the leaf without updating this
    contract test."""
    text = _DEPENDENCIES_PY.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(_DEPENDENCIES_PY))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.db.config.users":
                    found = True
                    break
        elif isinstance(node, ast.ImportFrom):
            if node.module == "app.db.config.users":
                found = True
                break
            if node.module == "app.db.config":
                for alias in node.names:
                    if alias.name == "users":
                        found = True
                        break
    assert found, (
        "REGRESSIONE BE-GO-READY-AUTH-ENGINE-015: "
        "`app/core/auth/dependencies.py` non importa piu' "
        "`app.db.config.users` (canonical leaf per `get_user`). "
        "Se la chiamata e' stata spostata altrove, aggiornare "
        "questa asserzione + la docstring del modulo."
    )
