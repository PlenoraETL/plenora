"""
Tests the api connector security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.arricchisci.execution_context import ArricchisciExecutionContext


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.security, pytest.mark.regression]


# BE-GO-READY-TRANSFORMATION-ENGINE-022: helper di costruzione context
# tipato per i 5 site MIGRATE_TO_CTX. Sostituisce il pattern legacy
# `api_service._current_group_id = X` (singleton mutation) con il
# typed boundary `ctx=ArricchisciExecutionContext(...)`. Behavior
# byte-per-byte identico: i 6 mixin reader site (typed context contract) leggono
# `ctx.group_id` quando il caller passa `ctx=`. Il singleton-state
# fallback resta KEEP_COMPAT (vedi i lock test
# `test_resolve_file_source_id_bytes_fails_closed_without_security_context`
# che NON setta il ctx — esplicito KEEP_AS_LEGACY_LOCK).
def _ctx(group_id: str | None = "grp-1", project_id: str = "p-1",
         preview_limit: int | None = None) -> ArricchisciExecutionContext:
    return ArricchisciExecutionContext(
        project_id=project_id,
        group_id=group_id,
        preview_limit=preview_limit,
    )


def test_mask_credentials_hides_all_sensitive_auth_fields(api_service):
    config = {
        "auth": {
            "token": "tok",
            "password": "pwd",
            "client_secret": "secret",
            "key_value": "api-key",
            "value": "api-key",
        }
    }

    masked = api_service._mask_credentials(config)

    assert masked["auth"]["token"] == "***"
    assert masked["auth"]["password"] == "***"
    assert masked["auth"]["client_secret"] == "***"
    assert masked["auth"]["key_value"] == "***"
    assert masked["auth"]["value"] == "***"
    assert config["auth"]["token"] == "tok"


def test_assert_file_source_group_access_fails_closed_without_context(api_service):
    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._assert_file_source_group_access("fs-1")


def test_assert_file_source_group_access_blocks_invisible_resource(api_service):
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    with patch("app.core.files.source_loading.is_resource_visible", return_value=False):
        with pytest.raises(ValueError, match="Accesso negato"):
            api_service._assert_file_source_group_access("fs-1", ctx=_ctx())


def test_assert_file_source_group_access_wraps_visibility_backend_errors(api_service):
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    with patch("app.core.files.source_loading.is_resource_visible", side_effect=RuntimeError("db error")):
        with pytest.raises(ValueError, match="Errore verifica accesso file_source"):
            api_service._assert_file_source_group_access("fs-1", ctx=_ctx())


def test_resolve_file_source_id_bytes_fails_closed_without_security_context(api_service):
    """BE-GO-READY-ORCHESTRATION-ENGINE-010: post-cut del fallback
    singleton, una chiamata senza `ctx` con `_current_group_id=None`
    fail-closed perche' `_extract_file_source_group_id` ritorna
    `None` direttamente (non legge piu' lo stato singleton)."""
    api_service._current_group_id = None

    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._resolve_file_source_id_bytes("fs-1")


def test_resolve_file_source_id_bytes_no_longer_falls_back_to_current_group_id(api_service):
    """BE-GO-READY-ORCHESTRATION-ENGINE-010 (cut lock): Current contract
    il fallback `ctx=None → getattr(self, "_current_group_id",
    None)` e' rimosso. Anche se `_current_group_id` e' settato a
    un valore non vuoto (es. residuo di una run precedente nello
    stesso processo worker), una chiamata SENZA `ctx` DEVE
    fail-closed — il singleton stale non puo' piu' autorizzare
    una risorsa.

    Pre-010 questo stesso input avrebbe propagato `"stale-leak"`
    a `is_resource_visible` e — se `is_resource_visible` avesse
    ritornato True per quel gruppo — autorizzato la materializzazione
    del blob. Post-010 il singleton e' invisibile alla catena
    file_source."""
    # Singleton stale che NON deve essere consultato.
    api_service._current_group_id = "stale-leak-grp"

    visibility_called = {"count": 0}

    def _spy(*_args, **_kwargs):
        visibility_called["count"] += 1
        return True  # se mai chiamata, autorizzerebbe — ma non DEVE essere chiamata.

    with patch("app.core.files.source_loading.is_resource_visible", side_effect=_spy):
        with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
            api_service._resolve_file_source_id_bytes("fs-1")

    assert visibility_called["count"] == 0, (
        "is_resource_visible NON deve essere chiamata: il fail-closed "
        "deve scattare a monte, prima che il singleton stale possa "
        "raggiungere la visibility check. Pre-010 la chiamata sarebbe "
        f"arrivata con group_id='stale-leak-grp' (count "
        f"{visibility_called['count']})."
    )


# BE-GO-READY-TRANSFORMATION-ENGINE-024: ctx-strict file-source path


def test_assert_file_source_group_access_ctx_wins_over_stale_singleton(api_service):
    """TE-024 lock: quando `ctx` e' fornito, `ctx.group_id` vince
    SEMPRE su `svc._current_group_id` stale. La visibility check
    deve essere chiamata con `ctx.group_id`, non con il valore
    legacy. Garantisce che un singleton stale (race window /
    leak fra worker pickup precedenti) NON apra un canale
    cross-tenant quando il caller ha gia' dichiarato il contesto
    tipato."""
    # Stato legacy stale (es. residuo di una run precedente).
    api_service._current_group_id = "stale-grp-A"

    # Ctx esplicito con il gruppo "corretto" della run corrente.
    ctx_winner = _ctx(group_id="winner-grp-B")

    captured_args = []

    def _capture_visibility(table, file_source_id, group_id, **kwargs):
        captured_args.append((table, file_source_id, group_id))
        # `winner-grp-B` ha visibilita' sulla risorsa.
        return group_id == "winner-grp-B"

    with patch("app.core.files.source_loading.is_resource_visible", side_effect=_capture_visibility):
        # Niente raise: la visibility check passa con ctx.group_id.
        api_service._assert_file_source_group_access("fs-1", ctx=ctx_winner)

    assert captured_args == [("file_sources", "fs-1", "winner-grp-B")], (
        f"Expected is_resource_visible chiamato con winner-grp-B; "
        f"got {captured_args}"
    )


def test_assert_file_source_group_access_ctx_with_none_group_does_not_fall_back_to_singleton(api_service):
    """TE-024 lock: quando `ctx` e' fornito ma `ctx.group_id` e'
    None, il path file-source DEVE fail-closed senza fallback su
    `_current_group_id`. Senza questo lock, un caller che passa
    `ctx=ArricchisciExecutionContext(group_id=None, ...)`
    (progetto inesistente / lookup fallito) cadrebbe in modo
    silente sul singleton stale, aprendo un canale cross-tenant
    semantico."""
    # Singleton stale che NON deve essere consultato.
    api_service._current_group_id = "stale-grp-leak"

    # Ctx esplicito con group_id=None (ramo dati legittimo: progetto
    # inesistente in DB → ExecutionContext.build_execution_context
    # ritorna group_id=None).
    ctx_unknown = _ctx(group_id=None)

    visibility_called = {"count": 0}

    def _track_visibility(*args, **kwargs):
        visibility_called["count"] += 1
        return True  # se mai chiamato, sarebbe gia' un bug

    with patch("app.core.files.source_loading.is_resource_visible", side_effect=_track_visibility):
        with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
            api_service._assert_file_source_group_access("fs-1", ctx=ctx_unknown)

    assert visibility_called["count"] == 0, (
        "is_resource_visible non deve essere chiamato quando ctx.group_id e' None — "
        "fail-closed PRIMA di consultare il singleton legacy."
    )


def test_resolve_file_source_id_bytes_ctx_wins_over_stale_singleton(api_service, monkeypatch):
    """TE-024 lock end-to-end: il livello superiore
    `_resolve_file_source_id_bytes` propaga ctx fino alla
    visibility check, e ctx.group_id vince su singleton stale.
    Cache hit-path NON viene esercitato: monkey-patcha
    `is_resource_visible` per ritornare True solo per
    winner-grp-B, e mock del DB blob per evitare I/O reale."""
    # source_loading e' il consumer reale di is_resource_visible: per
    # patcharlo serve il binding sul consumer, non sull'origine.
    import app.core.files.source_loading as sharing_mod
    import app.core.files.db as file_sources_db

    # Stato legacy stale.
    api_service._current_group_id = "stale-grp-A"

    # Mock del DB blob lookup.
    monkeypatch.setattr(
        file_sources_db,
        "get_file_source",
        lambda fs_id, include_data=False: {
            "original_filename": "input.bin",
            "data": b"abc" if include_data else None,
        },
    )
    monkeypatch.setattr(
        "app.core.resources.file_source_blob_access.load_file_source_blob_bytes",
        lambda **kwargs: b"abc",
    )

    seen_group_ids = []

    def _visibility_lock(table, file_source_id, group_id, **kwargs):
        seen_group_ids.append(group_id)
        return group_id == "winner-grp-B"

    monkeypatch.setattr(sharing_mod, "is_resource_visible", _visibility_lock)

    # Ctx esplicito vince su singleton stale.
    result = api_service._resolve_file_source_id_bytes(
        "fs-1", ctx=_ctx(group_id="winner-grp-B"),
    )

    assert seen_group_ids == ["winner-grp-B"], (
        f"Expected visibility check con winner-grp-B; got {seen_group_ids}"
    )
    assert result is not None, "blob materialization deve aver successo per winner-grp-B"


# BE-GO-READY-TRANSFORMATION-ENGINE-026: ctx-strict project_source path


def test_resolve_file_source_path_project_source_ctx_wins_over_stale_singleton(api_service, monkeypatch):
    """TE-026 lock: nel branch `project_source` di
    `_resolve_file_source_path`, quando `ctx` e' fornito,
    `ctx.group_id` vince SEMPRE su `svc._current_group_id` stale.
    `SourceLoader.resolve_project_file_bytes` deve ricevere
    `expected_group_id=ctx.group_id`, non il valore legacy.

    Post-Phase5: il loader ritorna `(bytes, filename)` invece di un
    path (path-mode rimosso). Senza questo, un singleton stale
    (residuo di una run precedente) farebbe attribuire un'eventuale
    block cross-tenant al group sbagliato — confondendo log e
    diagnosi."""
    # Stato legacy stale.
    api_service._current_group_id = "stale-grp-A"

    # Canvas con un edge da project_source verso un nodo target.
    nodes = [
        {"id": "src", "type": "project_source",
         "data": {"source_project_id": "proj-X"}},
        {"id": "tgt", "type": "rest_file"},
    ]
    edges = [{"source": "src", "target": "tgt"}]

    captured = []

    class _FakeLoader:
        def resolve_project_file_bytes(self, ref, expected_group_id):
            captured.append({
                "project_id": getattr(ref, "project_id", None),
                "expected_group_id": expected_group_id,
            })
            return (b"fake-bytes", "project_output.bin")

    # Patch del symbol al sito di lazy-import (`from app.core.artifacts
    # import ... SourceLoader`). _resolve_file_source_path importa
    # `SourceLoader` direttamente nel body, quindi patch sul module
    # `app.core.artifacts`.
    # api_connection_rest_file fa `from app.core.artifacts import SourceLoader`,
    # quindi il binding va patchato sul consumer.
    import app.core.arricchisci.api_connection_rest_file as rest_file_mod
    monkeypatch.setattr(rest_file_mod, "SourceLoader", _FakeLoader)

    # Ctx esplicito con il group "winner".
    result = api_service._resolve_file_source_path(
        nodes, edges, "tgt", ctx=_ctx(group_id="winner-grp-B"),
    )

    assert result == (b"fake-bytes", "project_output.bin")
    assert len(captured) == 1, (
        f"Expected 1 SourceLoader.resolve_project_file_bytes call; got {len(captured)}"
    )
    assert captured[0]["expected_group_id"] == "winner-grp-B", (
        f"Expected expected_group_id=winner-grp-B; got "
        f"{captured[0]['expected_group_id']!r} (singleton leak suspected)"
    )
    assert captured[0]["project_id"] == "proj-X"


def test_resolve_file_source_path_project_source_ctx_with_none_group_does_not_fall_back_to_singleton(api_service, monkeypatch):
    """TE-026 lock: quando il caller passa `ctx` con
    `group_id=None`, il branch `project_source` DEVE fail-closed
    senza fallback su `_current_group_id`. Senza questo, una
    `ArricchisciExecutionContext(project_id=..., group_id=None,
    ...)` (ramo dati legittimo: progetto inesistente in DB)
    cadrebbe in modo silente sul singleton di una run precedente,
    aprendo un canale cross-tenant."""
    # Singleton stale che NON deve essere consultato.
    api_service._current_group_id = "stale-grp-leak"

    nodes = [
        {"id": "src", "type": "project_source",
         "data": {"source_project_id": "proj-X"}},
        {"id": "tgt", "type": "rest_file"},
    ]
    edges = [{"source": "src", "target": "tgt"}]

    loader_called = {"count": 0}

    class _FakeLoader:
        def resolve_project_file_bytes(self, ref, expected_group_id):
            loader_called["count"] += 1
            return (b"should-never-be-returned", "x.bin")

    # api_connection_rest_file fa `from app.core.artifacts import SourceLoader`,
    # quindi il binding va patchato sul consumer.
    import app.core.arricchisci.api_connection_rest_file as rest_file_mod
    monkeypatch.setattr(rest_file_mod, "SourceLoader", _FakeLoader)

    # Ctx esplicito con group_id=None (ramo dati legittimo).
    with pytest.raises(ValueError, match="Contesto di sicurezza non determinabile"):
        api_service._resolve_file_source_path(
            nodes, edges, "tgt", ctx=_ctx(group_id=None),
        )

    assert loader_called["count"] == 0, (
        "SourceLoader.resolve_project_file_bytes non deve essere chiamato "
        "quando ctx.group_id e' None — fail-closed PRIMA di consultare "
        "il singleton legacy."
    )


def test_resolve_file_source_id_bytes_requires_visibility_before_materializing_blob(api_service, monkeypatch):
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    # source_loading e' il consumer reale di is_resource_visible: per
    # patcharlo serve il binding sul consumer, non sull'origine.
    import app.core.files.source_loading as sharing_mod
    import app.core.files.db as file_sources_db

    monkeypatch.setattr(sharing_mod, "is_resource_visible", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        file_sources_db, "get_file_source", lambda *args, **kwargs: {"data": b"abc"}
    )

    with pytest.raises(ValueError, match="Accesso negato"):
        api_service._resolve_file_source_id_bytes("fs-1", ctx=_ctx())


# C-API-01: cross-tenant recompute via force_recalculate


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
    monkeypatch.setattr(
        api_service, "_upstream_blob_is_stale", lambda *_args, **_kwargs: False
    )
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    # source_project_id appartiene al tenant B, non a grp-A
    monkeypatch.setattr(
        "app.core.arricchisci.api_connection_source_loading.load_project_access_context",
        lambda project_id: SimpleNamespace(project_id=project_id, group_id="grp-B"),
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
                ctx=_ctx(group_id="grp-A"),
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
    monkeypatch.setattr(
        api_service, "_upstream_blob_is_stale", lambda *_args, **_kwargs: False
    )
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    monkeypatch.setattr(
        "app.core.arricchisci.api_connection_source_loading.load_project_access_context",
        lambda *args, **kwargs: None,
    )

    with pytest.raises(ValueError, match="Accesso negato"):
        api_service._load_source_project(
            {"source_project_id": "proj-unknown"},
            force_recalculate=True,
            ctx=_ctx(group_id="grp-A"),
        )


def test_load_source_project_force_recalculate_allows_same_tenant(
    api_service, monkeypatch
):
    """Same-tenant con force_recalculate=True deve passare il pre-check
    e procedere al recompute. Se il pre-check fosse troppo stretto,
    questo test lo catturerebbe."""
    monkeypatch.setattr(
        api_service, "_upstream_blob_is_stale", lambda *_args, **_kwargs: False
    )
    # BE-GO-READY-TRANSFORMATION-ENGINE-022: typed ctx typed context contract.
    monkeypatch.setattr(
        "app.core.arricchisci.api_connection_source_loading.load_project_access_context",
        lambda project_id: SimpleNamespace(project_id=project_id, group_id="grp-A"),
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
                ctx=_ctx(group_id="grp-A"),
            )

    assert execute_called["count"] == 1, (
        "same-tenant recompute deve arrivare a execute_pipeline"
    )
