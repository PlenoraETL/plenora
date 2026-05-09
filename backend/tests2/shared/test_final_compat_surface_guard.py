"""Final anti-resurrection guard for legacy compatibility surfaces.

This is intentionally test-only and source-based. The allowlists start empty:
only residues explicitly declared unavoidable by a worker should be added here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_PROD_ROOTS = (_APP_ROOT, _BACKEND_ROOT / "workers")

_ALLOWED_KEEP_COMPAT_MARKERS: frozenset[str] = frozenset()
_ALLOWED_GETATTR_BRIDGES: frozenset[str] = frozenset()
_ALLOWED_CORE_DB_FACADE_IMPORTS: frozenset[str] = frozenset()

_LEGACY_CORE_DB_FACADES: frozenset[str] = frozenset()


def _rel(path: Path) -> str:
    return path.relative_to(_BACKEND_ROOT).as_posix()


def _iter_prod_python() -> list[Path]:
    paths: list[Path] = []
    for root in _PROD_ROOTS:
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return sorted(paths)


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8-sig"))


def _import_edges(tree: ast.AST) -> set[str]:
    edges: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            edges.update(alias.name for alias in node.names)
            continue
        if isinstance(node, ast.ImportFrom) and node.module:
            edges.add(node.module)
            edges.update(f"{node.module}.{alias.name}" for alias in node.names)
    return edges


def _keep_compat_markers() -> set[str]:
    markers: set[str] = set()
    for path in _iter_prod_python():
        rel_path = _rel(path)
        for line_no, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(),
            start=1,
        ):
            if "KEEP_COMPAT" in line:
                markers.add(f"{rel_path}:{line_no}::{line.strip()}")
    return markers


def _is_getattr_guarded_path(path: Path) -> bool:
    rel_path = _rel(path)
    parts = set(path.relative_to(_APP_ROOT).parts) if path.is_relative_to(_APP_ROOT) else set()
    return (
        path.name == "__init__.py"
        or path.stem.startswith("query_")
        or "renderer" in path.stem
        or "renderers" in parts
        or "settings" in parts
        or path.name == "settings.py"
        or "services" in parts
        or path.name == "services.py"
        or rel_path.startswith("workers/")
        and (path.name == "__init__.py" or path.name == "services.py")
    )


def _getattr_bridges() -> set[str]:
    bridges: set[str] = set()
    for path in _iter_prod_python():
        if not _is_getattr_guarded_path(path):
            continue
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "__getattr__":
                bridges.add(f"{_rel(path)}:{node.lineno}::__getattr__")
    return bridges


def test_final_state_has_no_productive_keep_compat_markers():
    current = _keep_compat_markers()

    assert current == set(_ALLOWED_KEEP_COMPAT_MARKERS), (
        "Final state must not carry productive KEEP_COMPAT markers. "
        "Only worker-declared unavoidable residues may be allowlisted.\n"
        f"Unexpected: {sorted(current - _ALLOWED_KEEP_COMPAT_MARKERS)}\n"
        f"Stale allowlist rows: {sorted(_ALLOWED_KEEP_COMPAT_MARKERS - current)}"
    )


def test_final_state_has_no_getattr_bridges_in_legacy_surface_modules():
    current = _getattr_bridges()

    assert current == set(_ALLOWED_GETATTR_BRIDGES), (
        "__getattr__ compatibility bridges must stay absent from query "
        "renderers, packages, settings, and services modules.\n"
        f"Unexpected: {sorted(current - _ALLOWED_GETATTR_BRIDGES)}\n"
        f"Stale allowlist rows: {sorted(_ALLOWED_GETATTR_BRIDGES - current)}"
    )


def test_final_state_has_no_app_services_imports_in_production_code():
    violations = {
        f"{_rel(path)} -> {edge}"
        for path in _iter_prod_python()
        for edge in _import_edges(_parse(path))
        if edge == "app.services" or edge.startswith("app.services.")
    }

    assert not violations, (
        "Production code must not import tombstoned app.services facades.\n"
        f"Violations: {sorted(violations)}"
    )


def test_final_state_has_no_ticket_or_log_run_db_facades_in_core():
    violations = {
        f"{_rel(path)} -> {edge}"
        for path in sorted((_APP_ROOT / "core").rglob("*.py"))
        for edge in _import_edges(_parse(path))
        if any(edge == facade or edge.startswith(f"{facade}.") for facade in _LEGACY_CORE_DB_FACADES)
    }

    assert violations == set(_ALLOWED_CORE_DB_FACADE_IMPORTS), (
        "Core code must use domain-owned ticket/log boundaries instead of "
        "legacy app.db tickets/log_runs facades.\n"
        f"Unexpected: {sorted(violations - _ALLOWED_CORE_DB_FACADE_IMPORTS)}\n"
        f"Stale allowlist rows: {sorted(_ALLOWED_CORE_DB_FACADE_IMPORTS - violations)}"
    )
