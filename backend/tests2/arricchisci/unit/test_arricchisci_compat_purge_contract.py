"""Arricchisci tombstone guards for legacy compatibility paths."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ARRICCHISCI_ROOT = _BACKEND_ROOT / "app" / "core" / "arricchisci"
_ARRICCHISCI_TEST_ROOT = _BACKEND_ROOT / "tests2" / "arricchisci"


def _python_files(root: Path) -> list[Path]:
    return [path for path in root.rglob("*.py") if "__pycache__" not in path.parts]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_arricchisci_scope_has_no_app_services_imports() -> None:
    violations: list[str] = []
    for path in [*_python_files(_ARRICCHISCI_ROOT), *_python_files(_ARRICCHISCI_TEST_ROOT)]:
        for module in _imports(path):
            if module == "app.services" or module.startswith("app.services."):
                violations.append(f"{path.relative_to(_BACKEND_ROOT)} imports {module}")

    assert not violations


def test_arricchisci_db_exposes_only_typed_api_connection_boundary() -> None:
    from app.core.arricchisci import db as api_db

    removed = {
        "_api_connection_compat_payload",
        "get_api_connection",
        "list_api_connections",
        "save_api_connection",
        "create_api_connection",
        "update_api_connection",
    }
    assert not (removed & set(vars(api_db)))


def test_enrichment_service_does_not_reexport_rate_limiter_compat_alias() -> None:
    import app.core.arricchisci.enrichment_service as enrichment_service
    from app.core.arricchisci._rate_limiter import RateLimiter

    assert not hasattr(enrichment_service, "RateLimiter")
    assert hasattr(enrichment_service, "_RateLimiter")
    assert enrichment_service._RateLimiter is RateLimiter


def test_arricchisci_core_has_no_keep_compat_markers() -> None:
    violations: list[str] = []
    for path in _python_files(_ARRICCHISCI_ROOT):
        text = path.read_text(encoding="utf-8-sig")
        if "KEEP_COMPAT" in text:
            violations.append(str(path.relative_to(_BACKEND_ROOT)))

    assert not violations


def test_api_connection_service_does_not_expose_context_init_adapter() -> None:
    from app.core.arricchisci.api_connector import ApiConnectionService

    assert not hasattr(ApiConnectionService, "_init_execution_context")
