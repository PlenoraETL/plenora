"""Guards for scheduler archive horizontalization."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import (
    all_identifiers,
    call_has_keyword,
    calls_method,
    uses_attribute,
)


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_EXECUTION_MANAGER = _BACKEND_ROOT / "app" / "core" / "scheduler" / "execution_manager.py"
_ROUTER = _BACKEND_ROOT / "app" / "core" / "scheduler" / "router.py"


def test_execution_manager_does_not_archive_schedule_outputs_to_results_dir():
    source = _EXECUTION_MANAGER.read_text(encoding="utf-8")

    # AST-bound: niente uso come codice della classe store, della copia file,
    # ne' chiamate .archive(). RAW solo per il pattern di costruzione path
    # (espressione, non identificatore).
    assert "ScheduleArtifactStore" not in all_identifiers(source)
    assert not uses_attribute(source, "shutil", "copy2")
    assert "RESULTS_DIR) / project_id" not in source  # code fragment: resta raw
    assert not calls_method(source, "archive")


def test_scheduler_router_download_uses_result_blob_boundary():
    source = _ROUTER.read_text(encoding="utf-8")

    assert "_download_result_file" in all_identifiers(source)
    assert uses_attribute(source, "_results_store", "get_result_data")
    assert call_has_keyword(source, "get_result_data", "group_id")
