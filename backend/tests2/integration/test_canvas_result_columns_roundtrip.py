"""
Integration test C9 — canvas round-trip ResultNode columns.

Pre-C9 bug: il PUT /canvas salvava `result_node.columns` as-is dal
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.core.projects.router import update_project_canvas
from app.core.projects.canvas_models import CanvasState, Node, Edge, Position, Column


pytestmark = [pytest.mark.integration, pytest.mark.contract]


def _build_canvas_with_empty_result_columns():
    """Canvas arricchisci: file_source → api_enrich → result (vuoto).

    L'api_enrich ha output_mapping popolato. Il file_source ha columns.
    Il result.columns e' vuoto (bug del frontend serializer).
    """
    return [
        Node(
            id="src1",
            type="file_source",
            position=Position(x=0, y=0),
            columns=[
                Column(name="city", type="str"),
                Column(name="zip", type="str"),
            ],
            data={"filename": "test.csv"},
        ),
        Node(
            id="enr1",
            type="api_enrich",
            position=Position(x=200, y=0),
            columns=[],
            data={
                "output_mapping": [
                    {"column": "temperature", "type": "real"},
                    {"column": "weather_code", "type": "int"},
                ],
                "output_fields": [
                    {"name": "temperature", "type": "real"},
                    {"name": "weather_code", "type": "int"},
                ],
            },
        ),
        Node(
            id="res1",
            type="result",
            position=Position(x=400, y=0),
            columns=[],  # FIXME scenario: frontend ha inviato vuoto
            data={},
        ),
    ]


def _build_edges():
    # Linear chain: src1 → enr1 → res1 (topologia tipica arricchisci)
    return [
        Edge(id="e1", source="src1", target="enr1", sourceHandle=None, targetHandle=None),
        Edge(id="e2", source="enr1", target="res1", sourceHandle=None, targetHandle=None),
    ]


class _FakeCurrentUser:
    role = "admin"
    user_id = "u1"
    username = "test"
    group_id = "g1"


def _fake_metadata(project_id: str) -> dict:
    return {"id": project_id, "name": "test", "group_id": "g1", "owner_id": "u1"}


def test_put_canvas_injects_computed_columns_into_result_node():
    """Scenario bug: canvas con result.columns=[] + api_enrich upstream.
    Post-C9 update_canvas deve ricevere un canvas con result.columns
    popolato dalle output_mapping (temperature, weather_code) + source cols."""
    nodes = _build_canvas_with_empty_result_columns()
    edges = _build_edges()

    # CanvasWithPlugins body — usiamo il dict shape diretto
    from app.core.projects.router import CanvasWithPlugins
    body = CanvasWithPlugins(nodes=nodes, edges=edges, plugins=None)

    # Cattura il canvas passato a update_canvas per verificare il risultato
    captured: dict = {}

    def capture_update_canvas(project_id, canvas):
        captured["canvas"] = canvas
        return True

    fake_project_service = MagicMock()
    fake_project_service.get_project_metadata.return_value = _fake_metadata("p1")
    fake_project_service.update_canvas.side_effect = capture_update_canvas
    fake_project_service.update_project_columns_metadata.return_value = None

    # Patch moduli level dei service nel router
    with patch("app.core.projects.router._project_service", fake_project_service):
        # Chiamata diretta endpoint function
        resp = update_project_canvas(
            project_id="p1", body=body, current_user=_FakeCurrentUser(),
        )

    assert resp.get("status") == "ok"
    canvas = captured.get("canvas")
    assert canvas is not None, "update_canvas non invocato"

    result_node = next((n for n in canvas.nodes if n.type == "result"), None)
    assert result_node is not None

    # POST-FIX: result_node.columns NON deve essere vuoto.
    # Pre-fix: era [] → bug UI.
    col_names = [c.name for c in (result_node.columns or [])]
    assert len(col_names) > 0, (
        f"REGRESSIONE C9: result_node.columns vuoto dopo PUT, "
        f"il backend non ha ricomputato. canvas: {canvas}"
    )
    # Le colonne provenienti dall'output_mapping dell'api_enrich devono esserci
    assert "temperature" in col_names, (
        f"columns da api_enrich.output_mapping mancanti: {col_names}"
    )
    assert "weather_code" in col_names, (
        f"columns da api_enrich.output_mapping mancanti: {col_names}"
    )


def test_put_canvas_preserves_user_alias_on_recompute():
    """Se l'utente ha personalizzato alias/visible, il recompute deve
    preservarli matchando per nome colonna originale."""
    nodes = _build_canvas_with_empty_result_columns()
    # Sovrascrivi result con customizzazioni utente
    nodes[-1] = Node(
        id="res1",
        type="result",
        position=Position(x=400, y=0),
        columns=[
            Column(name="temperature", alias="Temp (°C)", visible=True, type="real"),
            Column(name="city", alias=None, visible=False, type="str"),
        ],
        data={},
    )
    edges = _build_edges()

    from app.core.projects.router import CanvasWithPlugins
    body = CanvasWithPlugins(nodes=nodes, edges=edges, plugins=None)

    captured: dict = {}
    def capture_update_canvas(project_id, canvas):
        captured["canvas"] = canvas
        return True

    fake_project_service = MagicMock()
    fake_project_service.get_project_metadata.return_value = _fake_metadata("p1")
    fake_project_service.update_canvas.side_effect = capture_update_canvas
    fake_project_service.update_project_columns_metadata.return_value = None

    with patch("app.core.projects.router._project_service", fake_project_service):
        update_project_canvas(
            project_id="p1", body=body, current_user=_FakeCurrentUser(),
        )

    canvas = captured["canvas"]
    result_node = next(n for n in canvas.nodes if n.type == "result")
    by_name = {c.name: c for c in result_node.columns}

    assert "temperature" in by_name
    assert by_name["temperature"].alias == "Temp (°C)", (
        "alias utente non preservato dopo recompute"
    )
    assert "city" in by_name
    assert by_name["city"].visible is False, (
        "visible=False utente non preservato dopo recompute"
    )
