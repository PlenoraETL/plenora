"""Contracts for import hygiene cleanup on internal app shims."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_APP = Path(__file__).resolve().parents[2] / "app"


def _read(relative: str) -> str:
    return (_BACKEND_APP / relative).read_text(encoding="utf-8")


def test_file_service_does_not_fallback_to_local_model_shims() -> None:
    src = _read("core/files/service.py")

    assert "except ImportError" not in src
    assert "class FileSource:" not in src
    assert "class ColumnInfo:" not in src
    assert "_AppFileSource" not in src
    assert "_AppColumnInfo" not in src
    assert "_USE_APP_MODELS" not in src


def test_dataframe_schema_uses_canonical_column_info_model() -> None:
    src = _read("core/dataframes/schema.py")

    assert "from app.core.projects.canvas_models import ColumnInfo" in src
    assert "except ImportError" not in src
    assert "fallback dataclass" not in src


def test_ticket_metrics_does_not_mask_internal_import_failures() -> None:
    src = _read("core/admin_system_status/tickets_metrics.py")

    assert "_TICKETS_IMPORT_ERROR" not in src
    assert "except Exception as exc" not in src


def test_selected_stdlib_imports_are_not_repeated_inside_functions() -> None:
    """Small guard for stdlib imports already cleaned up.

    This is intentionally narrow: optional/heavy dependencies and app.* lazy
    imports are runtime boundaries, not covered by this guard.
    """
    expected_top_level: dict[str, set[str]] = {
        "core/arricchisci/api_connection_rest_file.py": {"re"},
        "core/carica/executor.py": {"time"},
        "core/dataframes/export_writer.py": {"json"},
        "core/dataframes/preview_loader.py": {"json"},
        "core/datasources/ftp.py": {"queue", "threading"},
        "core/datasources/service.py": {"os"},
        "core/datasources/sharepoint.py": {"os", "urllib.parse"},
        "core/eseguibili/db.py": {"datetime"},
        "core/eseguibili/service.py": {"hashlib"},
        "core/estrai/query_filters.py": {"re"},
    }

    violations: list[tuple[str, int, str]] = []
    for relative, modules in expected_top_level.items():
        source = _read(relative)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            else:
                module = node.names[0].name
            if module not in modules:
                continue
            if not any(child is node for child in tree.body):
                violations.append((relative, node.lineno, module))

    assert not violations


def test_selected_light_app_imports_are_not_repeated_inside_functions() -> None:
    """Guard app imports that were duplicated and are not optional boundaries."""
    expected_top_level: dict[str, set[str]] = {
        "core/arricchisci/_rate_limiter.py": {"app.config"},
        "core/carica/conflict_request_loader.py": {
            "app.core.dataframes.tabular"
        },
        "observability/health.py": {"app.config"},
    }

    violations: list[tuple[str, int, str]] = []
    for relative, modules in expected_top_level.items():
        source = _read(relative)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            else:
                module = node.names[0].name
            if module not in modules:
                continue
            if not any(child is node for child in tree.body):
                violations.append((relative, node.lineno, module))

    assert not violations
