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
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = None
    dataframes = {}

    result = executor._carica_sorgenti_file(_canvas_with_file_source(), dataframes)

    assert result == {
        "success": False,
        "message": "Contesto di sicurezza non determinabile",
    }
    assert dataframes == {}


def test_load_file_sources_blocks_invisible_cross_tenant_file():
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "grp-1"
    dataframes = {}

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        result = executor._carica_sorgenti_file(_canvas_with_file_source("fs-99"), dataframes)

    assert result == {
        "success": False,
        "message": "Accesso negato: il file sorgente 'fs-99' non appartiene al tuo gruppo",
    }
    assert dataframes == {}


def test_load_project_sources_blocks_cross_tenant_source_project():
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "grp-1"
    executor.results_base_dir = None
    dataframes = {}
    canvas = build_canvas(
        build_node("project_source", source_project_id="proj-99", columns=[]),
    )

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        result = executor._carica_sorgenti_progetto(canvas, dataframes, force_recalculate=False)

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

    with patch(
        "app.db.db_service.get_project",
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
