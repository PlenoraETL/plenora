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
         patch("app.core.carica.router._create_engine", return_value=object()), \
         patch("app.core.carica.conflict_checker.ConflictChecker", return_value=SimpleNamespace(check=lambda *args, **kwargs: report)), \
         patch("app.shared.authz_helpers.assert_project_visible") as assert_project_visible, \
         patch("app.core.artifacts.SourceLoader", return_value=fake_loader):
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

    # `_load_upstream_data` con `force_recalculate=False` fa pre-cache
    # check via `import app.db.db_service as _db; _db.get_project(...)`.
    # Senza patch, `db_service.get_project` chiama `pg_connection.fetchone`
    # reale -> rischio access violation Windows quando DB non e' opt-in
    # (vedi review #5 Step 3 P1-05). L'import e' lazy dentro la funzione,
    # quindi il symbol viene risolto al call time: patchare l'origine
    # `app.db.db_service.get_project` e' sufficiente. Restituiamo None
    # per simulare "progetto non trovato in pre-cache" -> il flow procede
    # come previsto dal test (fail-closed su _project_group_id, execute
    # via orchestrator stub).
    with patch("app.utils.result_store.get_result_as_tempfile", return_value=None), \
         patch("app.db.db_service.get_project", return_value=None), \
         patch("app.core.projects.service.ProjectService", return_value=object()), \
         patch("app.core.connections.service.ConnectionService", return_value=object()), \
         patch("app.core.estrai.query_generator.QueryGenerator", return_value=object()), \
         patch("app.db.query_executor.create_executor", return_value=object()), \
         patch("app.core.features.features_service.FeaturesService", return_value=object()), \
         patch("app.utils.dataframe.save_dataframe"), \
         patch("app.core.orchestrator.service.OrchestratorService", return_value=orchestrator):
        result = _load_upstream_data(
            project_id="proj-1",
            canvas={},
            force_recalculate=False,
            output_format="parquet",
        )

    import workers.tasks.carica_task as ct

    assert result is None
    assert "_load_from_project_dir" not in dir(ct)
