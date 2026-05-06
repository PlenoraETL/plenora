from unittest.mock import patch

import pytest

from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.security, pytest.mark.regression]
def _canvas_with_file_source(file_source_id="fs-1"):
    return build_canvas(
        build_node("file_source", file_source_id=file_source_id, columns=[]),
    )


def test_load_file_sources_fails_closed_when_current_group_is_unknown():
    """BE-GO-READY-TRANSFORMATION-ENGINE-005: chain-local cut on the
    file_source branch. The singleton ``_current_group_id`` is no
    longer consulted for this chain; absent an explicit ``group_id``
    kwarg the loader fail-closes regardless of the legacy attribute."""
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = None
    dataframes = {}

    # Post-005: omitting `group_id=` is enough to fail-close;
    # `_current_group_id` no longer participates in the file chain.
    result = executor._carica_sorgenti_file(_canvas_with_file_source(), dataframes)

    assert result == {
        "success": False,
        "message": "Contesto di sicurezza non determinabile",
    }
    assert dataframes == {}


def test_load_file_sources_blocks_invisible_cross_tenant_file():
    """BE-GO-READY-TRANSFORMATION-ENGINE-005: production callers
    pass ``group_id`` explicitly (set in ``execute``/``preview`` via
    ``_init_security_context``). The cross-tenant denial path is now
    exercised by passing the explicit kwarg, mirroring the production
    call shape."""
    executor = ManipolaExecutor(project_service=object())
    dataframes = {}

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        result = executor._carica_sorgenti_file(
            _canvas_with_file_source("fs-99"), dataframes,
            group_id="grp-1",
        )

    assert result == {
        "success": False,
        "message": "Accesso negato: il file sorgente 'fs-99' non appartiene al tuo gruppo",
    }
    assert dataframes == {}


def test_load_file_sources_ignores_stale_singleton_post_005():
    """BE-GO-READY-TRANSFORMATION-ENGINE-005 (cut lock): even when
    ``_current_group_id`` carries a stale value from a previous run
    in the same worker process, the file_source chain MUST fail-close
    instead of using it as a fallback. Pre-005 the same input would
    have authorised the file_source via ``_resolve_executing_group_id``;
    post-005 the singleton is invisible to this chain.

    The lock is the symmetric counterpart of
    ``test_resolve_file_source_id_path_no_longer_falls_back_to_current_group_id``
    on the arricchisci side (ORCHESTRATION-ENGINE-010)."""
    executor = ManipolaExecutor(project_service=object())
    # Stale singleton that MUST NOT be consulted.
    executor._current_group_id = "stale-grp-LEAK"
    dataframes = {}

    visibility_called = {"count": 0}

    def _spy(*_args, **_kwargs):
        visibility_called["count"] += 1
        return True  # if ever called, would authorise -- but MUST NOT be called

    with patch("app.shared.sharing.is_resource_visible", side_effect=_spy):
        # No `group_id=` passed; pre-005 the loader would have used
        # `_current_group_id="stale-grp-LEAK"` to authorise the
        # file_source. Post-005 the chain fail-closes upstream.
        result = executor._carica_sorgenti_file(
            _canvas_with_file_source("fs-stale"), dataframes,
        )

    assert result == {
        "success": False,
        "message": "Contesto di sicurezza non determinabile",
    }
    assert dataframes == {}
    assert visibility_called["count"] == 0, (
        "is_resource_visible MUST NOT be called: the file chain fail-"
        "closes before the visibility check can run with the stale "
        "singleton's value. Pre-005 the call would have arrived with "
        f"group_id='stale-grp-LEAK' (count {visibility_called['count']})."
    )


def test_load_project_sources_blocks_cross_tenant_source_project():
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "stale-grp-DECOY"  # must not be consulted (TE-007 ctx-strict)
    executor.results_base_dir = None
    dataframes = {}
    canvas = build_canvas(
        build_node("project_source", source_project_id="proj-99", columns=[]),
    )

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=False, group_id="grp-1",
        )

    assert result == {
        "success": False,
        "message": "Accesso negato: il progetto sorgente 'proj-99' appartiene a un gruppo diverso.",
    }
    assert dataframes == {}


# =============================================================================
# F1: preview() deve inizializzare _current_group_id come execute()
# =============================================================================


def test_preview_initializes_current_group_id_like_execute():
    """Regression guard F1: senza inizializzare `_current_group_id`,
    `_carica_sorgenti_progetto` / `_carica_sorgenti_file` fail-closed
    (vedi i due test sopra). Quei fail-closed ritornano un error dict
    che `preview()` ignora silenziosamente, finendo con il messaggio
    fuorviante "Nessun dato sorgente disponibile" anche quando il
    canvas era valido. Il preview deve usare lo stesso lookup tenant
    di execute() prima di invocare i loader."""
    executor = ManipolaExecutor(project_service=object())
    canvas = build_canvas(
        build_node("project_source", source_project_id="proj-up", columns=[]),
        build_node("result"),
    )

    # Mock i loader per terminare presto: ci interessa solo che
    # `_current_group_id` sia stato settato PRIMA di chiamarli.
    captured = {"group_id": "<unset>"}

    def _capture_group_id_then_skip(*args, **kwargs):
        captured["group_id"] = getattr(executor, "_current_group_id", None)
        return None  # success path nei loader

    # BE-GO-READY-DB-ENGINE-009: il lookup `_resolve_group_id_for_project_id`
    # e' stato migrato al leaf `app.db.config.projects.get_project`;
    # patch sul nuovo simbolo canonico.
    with patch(
        "app.db.config.projects.get_project",
        return_value={"id": "proj-cur", "group_id": "grp-A"},
    ), patch.object(
        executor, "_carica_sorgenti_progetto",
        side_effect=_capture_group_id_then_skip,
    ), patch.object(
        executor, "_carica_sorgenti_file", return_value=None
    ), patch.object(
        executor, "_carica_sorgenti_statiche", return_value=None
    ):
        executor.preview("proj-cur", canvas, limit=10)

    assert captured["group_id"] == "grp-A", (
        "preview() deve settare _current_group_id PRIMA di chiamare i "
        "source loader; altrimenti questi fail-closed e preview ritorna "
        "'Nessun dato sorgente disponibile' anche su canvas validi"
    )
