from unittest.mock import patch

import pytest

from app.core.arricchisci.api_connector import ApiConnectionService
from app.core.manipola.executor import ManipolaExecutor
from tests2.helpers.builders import build_canvas, build_node


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.regression]


def _manipola_file_source_denied():
    """BE-GO-READY-TRANSFORMATION-ENGINE-005: chain-local cut on the
    file_source branch. The ``group_id`` is now passed explicitly
    (mirroring the production call shape from ``execute``/``preview``);
    the singleton ``_current_group_id`` no longer participates in this
    chain even if it carries a value."""
    executor = ManipolaExecutor(project_service=object())
    canvas = build_canvas(build_node("file_source", file_source_id="fs-99", columns=[]))
    dataframes = {}

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        result = executor._carica_sorgenti_file(
            canvas, dataframes, group_id="grp-1",
        )

    assert result == {
        "success": False,
        "message": "Accesso negato: il file sorgente 'fs-99' non appartiene al tuo gruppo",
    }


def _manipola_project_source_denied():
    """BE-GO-READY-TRANSFORMATION-ENGINE-007: ctx-strict. The
    ``group_id`` is passed explicitly (mirroring the production call
    shape from ``execute``/``preview``); the singleton
    ``_current_group_id`` is set as a decoy to verify it is NOT used."""
    executor = ManipolaExecutor(project_service=object())
    executor._current_group_id = "stale-grp-DECOY"  # must not be consulted
    executor.results_base_dir = None
    canvas = build_canvas(build_node("project_source", source_project_id="proj-99", columns=[]))
    dataframes = {}

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=False, group_id="grp-1",
        )

    assert result == {
        "success": False,
        "message": "Accesso negato: il progetto sorgente 'proj-99' appartiene a un gruppo diverso.",
    }


def _arricchisci_file_source_denied():
    """Arricchisci file_source is ctx-strict after
    ORCHESTRATION-ENGINE-010: pass typed ctx explicitly to exercise
    the cross-tenant denial path. Without ctx this chain fail-closes
    earlier with "Contesto di sicurezza non determinabile", which is
    covered by the dedicated fail-closed tests."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    service = ApiConnectionService.__new__(ApiConnectionService)
    service._current_group_id = "stale-grp-LEAK"
    ctx = ArricchisciExecutionContext(
        project_id="p-1", group_id="grp-1", preview_limit=None,
    )

    with patch("app.shared.sharing.is_resource_visible", return_value=False):
        with pytest.raises(ValueError, match="Accesso negato"):
            service._assert_file_source_group_access("fs-99", ctx=ctx)


def _arricchisci_project_source_denied():
    """BE-GO-READY-TRANSFORMATION-ENGINE-030B: ctx-strict; il
    callsite arricchisci usa `_group_id_from_ctx(ctx)` (no singleton
    fallback). Stale singleton 'stale-grp-LEAK' come decoy: il
    cross-tenant denial e' calcolato sul `ctx.group_id`."""
    from app.core.arricchisci.execution_context import (
        ArricchisciExecutionContext,
    )

    service = ApiConnectionService.__new__(ApiConnectionService)
    service._current_group_id = "stale-grp-LEAK"
    service.results_dir = None
    node_data = {"source_project_id": "proj-99"}
    ctx = ArricchisciExecutionContext(
        project_id="p-1", group_id="grp-1", preview_limit=None,
    )

    with patch("app.db.db_service.get_project", return_value={"id": "proj-99", "group_id": "grp-2"}):
        with pytest.raises(ValueError, match="Accesso negato: progetto sorgente 'proj-99'"):
            service._load_source_project(node_data, ctx=ctx)


@pytest.mark.parametrize(
    "scenario",
    [
        _manipola_file_source_denied,
        _manipola_project_source_denied,
        _arricchisci_file_source_denied,
        _arricchisci_project_source_denied,
    ],
    ids=[
        "manipola-file-source",
        "manipola-project-source",
        "arricchisci-file-source",
        "arricchisci-project-source",
    ],
)
def test_cross_tenant_access_is_denied_across_source_loaders(scenario):
    scenario()
