"""Guards for the object-storage infrastructure boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_APP_ROOT = _BACKEND_ROOT / "app"


def _rel(path: Path) -> str:
    return str(path.relative_to(_BACKEND_ROOT)).replace("\\", "/")


def _imports_legacy_cloud_backends(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "app.shared.cloud_backends":
                return True
            if module == "app.shared" and any(
                alias.name == "cloud_backends" for alias in node.names
            ):
                return True
        if isinstance(node, ast.Import):
            if any(alias.name == "app.shared.cloud_backends" for alias in node.names):
                return True
    return False


def test_backend_runtime_does_not_import_shared_cloud_backends() -> None:
    """Runtime modules must use ``app.infrastructure.storage.cloud_backends``.

    ``app.shared.cloud_backends`` is intentionally kept as a compatibility alias
    for older tests and external imports, not for new backend runtime code.
    """
    legacy_shim = _APP_ROOT / "shared" / "cloud_backends.py"
    offenders = []
    for path in _APP_ROOT.rglob("*.py"):
        if path == legacy_shim:
            continue
        try:
            if _imports_legacy_cloud_backends(path):
                offenders.append(_rel(path))
        except SyntaxError:
            continue
    assert offenders == []


def test_shared_cloud_backends_is_module_alias_shim() -> None:
    import app.infrastructure.storage.cloud_backends as infra_cloud_backends
    import app.shared.cloud_backends as shared_cloud_backends

    assert shared_cloud_backends is infra_cloud_backends
