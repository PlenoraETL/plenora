"""Guards for the object-storage infrastructure boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"
_TESTS_ROOT = _BACKEND_ROOT / "tests2"
_LEGACY_MODULE = "app.shared.cloud_backends"


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")


def _imports_legacy_cloud_backends(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _LEGACY_MODULE:
                return True
            if module == "app.shared" and any(
                alias.name == "cloud_backends" for alias in node.names
            ):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name == _LEGACY_MODULE for alias in node.names):
                return True
    return False


def test_backend_runtime_does_not_import_legacy_shared_cloud_backends() -> None:
    """Runtime modules must use ``app.infrastructure.storage.cloud_backends``.

    The historical ``app.shared.cloud_backends`` path is forbidden for backend
    runtime code.
    """
    offenders = []
    for path in _APP_ROOT.rglob("*.py"):
        try:
            if _imports_legacy_cloud_backends(path):
                offenders.append(_rel(path))
        except SyntaxError:
            continue
    assert offenders == []


def test_backend_tests_do_not_patch_or_import_legacy_shared_cloud_backends() -> None:
    """Tests must patch the infrastructure module, not the legacy shared path."""
    this_file = Path(__file__).resolve()
    offenders = []
    for path in _TESTS_ROOT.rglob("*.py"):
        if path.resolve() == this_file:
            continue
        text = path.read_text(encoding="utf-8")
        if _LEGACY_MODULE in text or "from app.shared import cloud_backends" in text:
            offenders.append(_rel(path))
    assert offenders == []
