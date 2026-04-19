from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.models import WriteStatus
from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract, pytest.mark.regression]


def _make_manager() -> ExecutionManager:
    manager = ExecutionManager.__new__(ExecutionManager)
    manager.schedule_service = MagicMock()
    manager.log_service = MagicMock()
    manager.concurrency = MagicMock()
    manager.policy = MagicMock()
    manager._connection_service = None
    manager._get_connection_service = MagicMock(return_value=MagicMock())
    return manager


def _make_manager_for_isolation_test(schedule_config) -> ExecutionManager:
    """ExecutionManager preconfigurato per testare il check di group
    isolation in `execute()`. La concurrency e' mockata in modo da non
    bloccare prima del check (anche se il check fail-closed dovrebbe
    rifiutare prima di arrivarci)."""
    manager = _make_manager()
    manager.schedule_service.get_schedule = MagicMock(return_value=schedule_config)
    manager.schedule_service.set_runtime_status = MagicMock()
    manager.concurrency.try_acquire = MagicMock(return_value="acquired")
    return manager


def _make_write_result(status=WriteStatus.SUCCESS, rows_written=2, total_rows=2):
    result = MagicMock()
    result.status = status
    result.rows_written = rows_written
    result.rows_inserted = rows_written
    result.rows_updated = 0
    result.total_rows = total_rows
    result.error_message = ""
    return result


def test_execute_carica_uses_result_id_artifact_before_output_path(schedule_config, tmp_path):
    manager = _make_manager()
    df = pd.DataFrame({"id": [1, 2]})
    stale_path = tmp_path / "stale.csv"
    stale_path.write_text("id\n999\n", encoding="utf-8")

    with patch("app.utils.dataframe.load_dataframe_from_file", return_value=df) as mock_load, \
         patch("app.core.carica.executor.CaricaExecutor") as mock_executor, \
         patch("app.db.db_service.get_result", return_value={"id": "res-1", "project_id": "proj-1", "format": "csv"}), \
         patch("app.db.db_service.get_result_data", return_value=b"id\n1\n2\n"):
        mock_executor.return_value.execute.return_value = _make_write_result()
        result = manager._execute_carica(
            schedule_config,
            {"result_id": "res-1", "output_path": str(stale_path)},
        )

    assert result["success"] is True
    assert mock_load.call_count == 1
    loaded_target = Path(mock_load.call_args.args[0])
    assert loaded_target.name != stale_path.name


def test_execute_carica_fails_when_no_run_specific_artifact_is_available(schedule_config):
    manager = _make_manager()

    result = manager._execute_carica(schedule_config, {})

    assert result["success"] is False
    assert "Artefatto della run non disponibile" in result["error"]


# =============================================================================
# Group isolation fail-closed (Refactor #2 invariante C)
# =============================================================================


def _project_meta(group_id):
    return SimpleNamespace(
        id="proj-1",
        group_id=group_id,
        project_type="estrai",
    )


def test_execute_blocks_cross_group_isolation(schedule_config):
    """Schedule (group=grp-A) tenta di eseguire progetto (group=grp-B) →
    hard fail con status='failed', niente esecuzione downstream."""
    manager = _make_manager_for_isolation_test(schedule_config)

    with patch("app.db.db_service.get_schedule", return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-B")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "group isolation" in result["message"].lower()
    # Concurrency NON deve essere acquisita: il check e' prima del lock
    manager.concurrency.try_acquire.assert_not_called()


def test_execute_blocks_schedule_without_group_id(schedule_config):
    """Schedule legacy senza group_id → fail-closed (era fail-OPEN
    pre-Refactor #2 a causa dello short-circuit del check)."""
    manager = _make_manager_for_isolation_test(schedule_config)

    with patch("app.db.db_service.get_schedule", return_value={"id": "s-1", "group_id": None}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-B")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "schedule senza group_id" in result["message"]
    manager.concurrency.try_acquire.assert_not_called()


def test_execute_blocks_project_without_group_id(schedule_config):
    """Progetto senza group_id assegnato → fail-closed.
    Era fail-OPEN pre-Refactor #2."""
    manager = _make_manager_for_isolation_test(schedule_config)

    with patch("app.db.db_service.get_schedule", return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("")
        result = manager.execute("s-1")

    assert result["success"] is False
    assert result["status"] == "failed"
    assert "senza group_id" in result["message"]
    manager.concurrency.try_acquire.assert_not_called()


def test_execute_passes_group_isolation_when_groups_match(schedule_config):
    """Sanity check: stesso group_id su schedule e progetto → il check
    di isolation passa e l'esecuzione procede al concurrency lock."""
    manager = _make_manager_for_isolation_test(schedule_config)
    # Concurrency ritorna 'queued' subito dopo il check di isolation,
    # cosi' evitiamo di mockare l'intera pipeline downstream.
    manager.concurrency.try_acquire.return_value = "queued"

    with patch("app.db.db_service.get_schedule", return_value={"id": "s-1", "group_id": "grp-A"}), \
         patch("app.services.project_service.ProjectService") as project_service_cls:
        project_service_cls.return_value.get_project_metadata.return_value = _project_meta("grp-A")
        result = manager.execute("s-1")

    # Group isolation passato: l'esecuzione e' arrivata almeno al lock
    manager.concurrency.try_acquire.assert_called_once()
    assert result["status"] == "queued"
