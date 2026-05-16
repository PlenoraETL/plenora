"""Guards for scheduler archive horizontalization."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_EXECUTION_MANAGER = _BACKEND_ROOT / "app" / "core" / "scheduler" / "execution_manager.py"
_ROUTER = _BACKEND_ROOT / "app" / "core" / "scheduler" / "router.py"


def test_execution_manager_does_not_archive_schedule_outputs_to_results_dir():
    source = _EXECUTION_MANAGER.read_text(encoding="utf-8")

    assert "ScheduleArtifactStore" not in source
    assert "shutil.copy2" not in source
    assert "RESULTS_DIR) / project_id" not in source
    assert ".archive(" not in source


def test_scheduler_router_download_uses_result_blob_boundary():
    source = _ROUTER.read_text(encoding="utf-8")

    assert "_download_result_file" in source
    assert "_results_store.get_result_data" in source
    assert "get_result_data(result_id, group_id=group_id)" in source
