"""
Tombstone contract -- `app.db.db_service` facade.

BE-GO-READY-DB-ENGINE-011-DB-SERVICE-DEATHMAP-CONTRACT.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP = _BACKEND_ROOT / "app"
_WORKERS = _BACKEND_ROOT / "workers"
_TESTS2 = _BACKEND_ROOT / "tests2"

_FACADE_DOTTED = "app.db.db_service"
_FACADE_PARENT = "app.db"
_FACADE_LEAF = "db_service"
_FACADE_FILE = "app/db/db_service.py"


# The facade is physically deleted. This guard is now a tombstone:
# production code and tests may not import it, tests may not patch it,
# and the deleted path may not be recreated as a compatibility shim.
_DB_SERVICE_PROD_ALLOWLIST: frozenset[str] = frozenset()


def _imports_facade(tree: ast.AST) -> bool:
    """True if an AST imports `app.db.db_service` in any legal shape."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _FACADE_DOTTED:
                    return True
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == _FACADE_DOTTED:
                return True
            if node.module == _FACADE_PARENT:
                return any(alias.name == _FACADE_LEAF for alias in node.names)
    return False


def _scan_prod_roots() -> list[Path]:
    return [root for root in (_APP, _WORKERS) if root.exists()]


def _scan_all_roots() -> list[Path]:
    return [root for root in (_APP, _WORKERS, _TESTS2) if root.exists()]


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _parse(path: Path) -> ast.AST | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _scan_importers(roots: list[Path]) -> set[str]:
    found: set[str] = set()
    for root in roots:
        for path in root.rglob("*.py"):
            tree = _parse(path)
            if tree is not None and _imports_facade(tree):
                found.add(_rel(path))
    return found


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        if prefix:
            return f"{prefix}.{node.attr}"
        return node.attr
    return ""


def _scan_patch_targets() -> set[str]:
    found: set[str] = set()
    for root in _scan_all_roots():
        for path in root.rglob("*.py"):
            tree = _parse(path)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                call_name = _call_name(node.func)
                if not (
                    call_name.endswith("patch")
                    or call_name.endswith("patch.object")
                    or call_name.endswith("setattr")
                ):
                    continue
                for arg in node.args[:2]:
                    if (
                        isinstance(arg, ast.Constant)
                        and isinstance(arg.value, str)
                        and _FACADE_DOTTED in arg.value
                    ):
                        found.add(f"{_rel(path)}:{node.lineno} -> {arg.value}")
    return found


def test_no_new_db_service_imports_outside_allowlist():
    """No production file may import the tombstoned facade."""
    found = _scan_importers(_scan_prod_roots())
    new = sorted(found - _DB_SERVICE_PROD_ALLOWLIST)
    assert not new, (
        "New imports of `app.db.db_service` detected. Migrate to the "
        "domain-owned `app.core.<domain>.db` boundary or DB infrastructure "
        "such as `app.db.pg_connection`.\nNew importers:\n  - "
        + "\n  - ".join(new)
    )


def test_db_service_allowlist_has_no_stale_entries():
    """The tombstone allowlist must remain empty."""
    found = _scan_importers(_scan_prod_roots())
    stale = sorted(_DB_SERVICE_PROD_ALLOWLIST - found)
    assert not stale, (
        "`_DB_SERVICE_PROD_ALLOWLIST` lists stale entries:\n  - "
        + "\n  - ".join(stale)
    )


def test_db_service_facade_file_is_tombstoned():
    """The deleted facade must not be reintroduced."""
    facade_path = _BACKEND_ROOT / _FACADE_FILE
    assert not facade_path.exists(), (
        f"`{_FACADE_FILE}` was physically deleted. Do not recreate "
        "the cross-engine facade; import the canonical leaf module instead."
    )


def test_no_db_service_imports_or_patch_targets_anywhere():
    """No production/test code may depend on the deleted facade."""
    importers = sorted(_scan_importers(_scan_all_roots()))
    patch_targets = sorted(_scan_patch_targets())
    assert not importers, (
        "Deleted facade `app.db.db_service` is imported again:\n  - "
        + "\n  - ".join(importers)
    )
    assert not patch_targets, (
        "Deleted facade `app.db.db_service` is patched again:\n  - "
        + "\n  - ".join(patch_targets)
    )
