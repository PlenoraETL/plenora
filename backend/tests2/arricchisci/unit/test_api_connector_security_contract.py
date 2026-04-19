from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.security, pytest.mark.regression]


def test_mask_credentials_hides_all_sensitive_auth_fields(api_service):
    config = {
        "auth": {
            "token": "tok",
            "password": "pwd",
            "client_secret": "secret",
            "key_value": "api-key",
        }
    }

    masked = api_service._mask_credentials(config)

    assert masked["auth"]["token"] == "***"
    assert masked["auth"]["password"] == "***"
    assert masked["auth"]["client_secret"] == "***"
    assert masked["auth"]["key_value"] == "***"
    assert config["auth"]["token"] == "tok"


def test_assert_file_source_group_access_fails_closed_without_context(api_service):
    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._assert_file_source_group_access("fs-1")


def test_assert_file_source_group_access_blocks_invisible_resource(api_service):
    api_service._current_group_id = "grp-1"

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        with pytest.raises(ValueError, match="Accesso negato"):
            api_service._assert_file_source_group_access("fs-1")


def test_assert_file_source_group_access_wraps_visibility_backend_errors(api_service):
    api_service._current_group_id = "grp-1"

    with patch("app.shared.sharing.is_resource_visible", side_effect=RuntimeError("db error")):
        with pytest.raises(ValueError, match="Errore verifica accesso file_source"):
            api_service._assert_file_source_group_access("fs-1")


def test_resolve_file_source_id_path_fails_closed_without_security_context(api_service):
    api_service._current_group_id = None

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._resolve_file_source_id_path("fs-1")


def test_resolve_file_source_id_path_requires_visibility_before_materializing_blob(api_service, monkeypatch, tmp_path):
    api_service._current_group_id = "grp-1"

    import app.config as config_mod
    import app.shared.sharing as sharing_mod
    import app.db.db_service as db_service

    monkeypatch.setattr(
        config_mod,
        "settings",
        type("S", (), {"get_file_source_dir": lambda self, file_source_id: tmp_path / file_source_id})(),
    )
    monkeypatch.setattr(sharing_mod, "is_resource_visible", lambda *args, **kwargs: False)
    monkeypatch.setattr(db_service, "get_file_source", lambda *args, **kwargs: {"data": b"abc"})

    with pytest.raises(ValueError, match="Accesso negato"):
        api_service._resolve_file_source_id_path("fs-1")


# =============================================================================
# C-API-01: cross-tenant recompute via force_recalculate
# =============================================================================


def test_load_source_project_force_recalculate_blocks_cross_tenant_before_recompute(
    api_service, monkeypatch
):
    """Regression guard per C-API-01.

    Con `force_recalculate=True` il Tentativo 1 (SourceLoader.load con
    cross-tenant check) viene saltato. Prima del fix, il codice
    chiamava direttamente OrchestratorService.execute_pipeline su un
    source_project_id arbitrario, applicando il check cross-tenant
    solo a recompute gia' avvenuta. Il pre-check fail-closed deve
    sollevare ValueError PRIMA che execute_pipeline venga invocato.
    """
    api_service._current_group_id = "grp-A"

    import app.db.config_db_service as cfg_db

    # source_project_id appartiene al tenant B, non a grp-A
    monkeypatch.setattr(
        cfg_db,
        "get_project",
        lambda project_id, group_id=None: {
            "id": project_id,
            "group_id": "grp-B",
        },
    )

    orchestrator_called = {"count": 0}

    def _fail_if_called(*args, **kwargs):
        orchestrator_called["count"] += 1
        raise AssertionError(
            "execute_pipeline NON deve essere chiamato cross-tenant "
            "(pre-check fail-closed)"
        )

    with patch(
        "app.core.orchestrator.service.OrchestratorService.execute_pipeline",
        side_effect=_fail_if_called,
    ):
        with pytest.raises(ValueError, match="Accesso negato"):
            api_service._load_source_project(
                {"source_project_id": "proj-tenant-B"},
                force_recalculate=True,
            )

    assert orchestrator_called["count"] == 0, (
        "cross-tenant recompute eseguito prima del check"
    )


def test_load_source_project_force_recalculate_blocks_unknown_project(
    api_service, monkeypatch
):
    """Se il source_project_id non esiste in DB, fail-closed: nessun
    fallthrough che possa diventare un probe per l'enumeration dei
    project_id cross-tenant."""
    api_service._current_group_id = "grp-A"

    import app.db.config_db_service as cfg_db
    monkeypatch.setattr(cfg_db, "get_project", lambda *args, **kwargs: None)

    with pytest.raises(ValueError, match="Accesso negato"):
        api_service._load_source_project(
            {"source_project_id": "proj-unknown"},
            force_recalculate=True,
        )


def test_load_source_project_force_recalculate_allows_same_tenant(
    api_service, monkeypatch
):
    """Same-tenant con force_recalculate=True deve passare il pre-check
    e procedere al recompute. Se il pre-check fosse troppo stretto,
    questo test lo catturerebbe."""
    api_service._current_group_id = "grp-A"

    import app.db.config_db_service as cfg_db
    monkeypatch.setattr(
        cfg_db,
        "get_project",
        lambda project_id, group_id=None: {
            "id": project_id,
            "group_id": "grp-A",
        },
    )

    # Mock OrchestratorService.execute_pipeline: deve essere chiamato.
    # Ritorniamo success=False cosi' da fermarci subito dopo senza
    # dover simulare load_dataframe_from_file et al.
    execute_called = {"count": 0}

    def _mock_execute(self, project_id, force_recalculate=False):
        execute_called["count"] += 1
        return {"success": False, "message": "stop dopo pre-check"}

    with patch(
        "app.core.orchestrator.service.OrchestratorService.execute_pipeline",
        new=_mock_execute,
    ):
        with pytest.raises(ValueError, match="Errore progetto sorgente"):
            api_service._load_source_project(
                {"source_project_id": "proj-same-tenant"},
                force_recalculate=True,
            )

    assert execute_called["count"] == 1, (
        "same-tenant recompute deve arrivare a execute_pipeline"
    )
