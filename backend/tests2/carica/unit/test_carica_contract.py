"""
Tests the carica behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from workers.tasks.carica_task import _load_upstream_data


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract, pytest.mark.regression]


def test_check_conflicts_checks_project_visibility_before_loading_project_output(client, current_user):
    report = SimpleNamespace(to_dict=lambda: {"has_conflicts": False, "total_conflicts": 0})
    df = pd.DataFrame([{"id": 1}])

    # Post-cleanup get_result_as_tempfile: il router ora passa per
    # SourceLoader. Patchiamo SourceLoader nel namespace del router
    # cosi' il flow non tocca db_service / resolver reale.
    fake_loader = SimpleNamespace(load=lambda *args, **kwargs: df)

    with patch("app.core.carica.router._get_connection", return_value={"type": "postgres"}), \
         patch("app.core.carica.router._assert_connection_visible"), \
         patch("app.core.carica.router._create_engine", return_value=object()), \
         patch("app.core.carica.conflict_checker.ConflictChecker", return_value=SimpleNamespace(check=lambda *args, **kwargs: report)), \
         patch("app.core.carica.router.assert_project_visible") as assert_project_visible, \
         patch("app.core.carica.router.SourceLoader", return_value=fake_loader):
        response = client.post(
            "/api/carica/check-conflicts",
            json={
                "connection_id": "conn-1",
                "schema_name": "public",
                "table_name": "dest",
                "match_columns": ["id"],
                "project_id": "proj-1",
            },
        )

    assert response.status_code == 200
    assert response.json()["has_conflicts"] is False
    assert_project_visible.assert_called_once_with("proj-1", current_user)


def test_load_upstream_data_does_not_fallback_to_latest_project_result_after_failed_recompute():
    orchestrator = SimpleNamespace(
        execute_pipeline=lambda **kwargs: {"success": True},
    )

    # Il tenant della cache e' quello congelato nel contratto CARICA: il
    # loader non deve fare un secondo lookup del progetto durante la run.
    fake_loader = SimpleNamespace(load=lambda *args, **kwargs: None)
    with patch("workers.tasks.carica_task.SourceLoader", return_value=fake_loader), \
         patch(
             "app.runtime.services.build_orchestrator_stack",
             return_value=SimpleNamespace(orchestrator=orchestrator),
         ):
        result = _load_upstream_data(
            project_id="proj-1",
            canvas={},
            force_recalculate=False,
            output_format="parquet",
            expected_group_id="group-1",
        )

    import workers.tasks.carica_task as ct

    assert result is None
    assert "_load_from_project_dir" not in dir(ct)
