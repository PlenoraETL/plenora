"""Contracts for import hygiene cleanup on internal app shims."""

from __future__ import annotations

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
