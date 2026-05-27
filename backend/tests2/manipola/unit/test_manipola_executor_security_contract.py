"""
Tests the manipola executor security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch
from pathlib import Path

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor
from app.core.projects.canvas_models import CanvasState
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
    dataframes = {}

    # Post-005: omitting `group_id=` is enough to fail-close.
    result = executor._carica_sorgenti_file(_canvas_with_file_source(), dataframes)

    assert result == {
        "success": False,
        "message": "Contesto di sicurezza non determinabile",
    }
    assert dataframes == {}


def test_security_context_resolver_is_injected_boundary():
    calls = []

    def resolver(project_id: str) -> str:
        calls.append(project_id)
        return "grp-injected"

    executor = ManipolaExecutor(
        project_service=object(),
        group_id_resolver=resolver,
    )

    assert executor._init_security_context("project-1", strict=True) == "grp-injected"
    assert calls == ["project-1"]


def test_load_file_sources_blocks_invisible_cross_tenant_file():
    """BE-GO-READY-TRANSFORMATION-ENGINE-005: production callers
    pass ``group_id`` explicitly (set in ``execute``/``preview`` via
    ``_init_security_context``). The cross-tenant denial path is now
    exercised by passing the explicit kwarg, mirroring the production
    call shape."""
    executor = ManipolaExecutor(project_service=object())
    dataframes = {}

    with patch("app.core.artifacts.source_loader.is_resource_visible", return_value=False):
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
    ``test_resolve_file_source_id_bytes_no_longer_falls_back_to_current_group_id``
    on the arricchisci side (ORCHESTRATION-ENGINE-010)."""
    executor = ManipolaExecutor(project_service=object())
    dataframes = {}

    visibility_called = {"count": 0}

    def _spy(*_args, **_kwargs):
        visibility_called["count"] += 1
        return True  # if ever called, would authorise -- but MUST NOT be called

    with patch("app.core.artifacts.source_loader.is_resource_visible", side_effect=_spy):
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
    from app.core.artifacts import ArtifactResolutionError

    executor = ManipolaExecutor(project_service=object())
    executor.results_base_dir = Path("/tmp/plenora-results-unused")
    dataframes = {}
    canvas = build_canvas(
        build_node("project_source", source_project_id="proj-99", columns=[]),
    )

    with patch(
        "app.core.artifacts.ArtifactResolver.materialize_latest",
        side_effect=ArtifactResolutionError("cross-tenant"),
    ):
        result = executor._carica_sorgenti_progetto(
            canvas, dataframes, force_recalculate=False, group_id="grp-1",
        )

    assert result == {
        "success": False,
        "message": "Accesso negato: il progetto sorgente 'proj-99' appartiene a un gruppo diverso.",
    }
    assert dataframes == {}


# F1: preview() deve passare group_id esplicito ai source loader (TE-009)


def test_preview_passes_group_id_explicitly_to_source_loaders():
    """TE-009 regression guard: preview() must resolve group_id via
    `_init_security_context` and pass it as an explicit kwarg to each
    source loader. No instance attribute is used; the kwarg is the only
    tenant channel."""
    executor = ManipolaExecutor(project_service=object())
    canvas = build_canvas(
        build_node("project_source", source_project_id="proj-up", columns=[]),
        build_node("result"),
    )

    captured = {"group_id": "<unset>"}

    def _capture_group_id_then_skip(*args, **kwargs):
        captured["group_id"] = kwargs.get("group_id")
        return None

    with patch(
        "app.core.manipola.executor.get_project_group_id",
        return_value="grp-A",
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
        "preview() must pass the resolved group_id as an explicit kwarg "
        "to _carica_sorgenti_progetto (TE-009 ctx-strict). "
        f"Got: {captured['group_id']!r}"
    )


def test_preview_builds_canvas_graph_once_at_intake_boundary():
    """Preview parses the canvas once via CanvasGraph.from_canvas and passes
    the parsed graph to local transform/type helpers."""
    from app.shared.execution.canvas_graph import CanvasGraph

    executor = ManipolaExecutor(project_service=object())
    canvas = CanvasState(
        nodes=[
            {
                "id": "src",
                "type": "staticValues",
                "position": {"x": 0, "y": 0},
                "fields": [{"name": "a", "value": "1"}],
                "columns": [{"name": "a", "type": "int"}],
            },
            {
                "id": "res",
                "type": "result",
                "position": {"x": 200, "y": 0},
                "format": "csv",
            },
        ],
        edges=[],
    )
    parsed_graph = CanvasGraph.from_canvas(canvas)
    df = pd.DataFrame({"a": [1]})

    from_canvas_calls = {"count": 0}

    def _from_canvas_once(arg):
        from_canvas_calls["count"] += 1
        assert arg is canvas
        return parsed_graph

    def _load_static(_canvas, dataframes, **_kwargs):
        dataframes["src"] = df
        return None

    with patch(
        "app.core.manipola.executor.get_project_group_id",
        return_value="grp-A",
    ), patch(
        "app.core.manipola.executor.CanvasGraph.from_canvas",
        side_effect=_from_canvas_once,
    ), patch.object(
        executor, "_carica_sorgenti_progetto", return_value=None,
    ), patch.object(
        executor, "_carica_sorgenti_file", return_value=None,
    ), patch.object(
        executor, "_carica_sorgenti_statiche", side_effect=_load_static,
    ), patch.object(
        executor, "_applica_trasformazioni", return_value=df,
    ) as transform_spy:
        result = executor.preview("proj-cur", canvas, limit=10)

    assert result["success"] is True
    assert from_canvas_calls["count"] == 1
    assert transform_spy.call_args.kwargs["canvas_graph"] is parsed_graph
    assert result["columns"] == [{"name": "a", "type": "int"}]
