"""Valida legge i parametri della connessione ATTUALE (Fase 5, T6, R2.9, R2.10, N11).

Il nodo api_enrich porta il riferimento (``api_id``); la copia di
``parameters``/``static_parameters`` salvata nel nodo puo' essere vecchia
(o ``[]``). ``with_live_api_details`` mette nella validazione i dettagli della
connessione, senza cambiare il canvas salvato.
"""

from __future__ import annotations

from app.core.arricchisci.node_validator import ArricchisciNodeValidator
from app.core.projects.canvas_models import CanvasState
from app.core.projects.router import with_live_api_details
from app.shared.execution.schema_types import InferredSchema


def _canvas(parameters):
    return CanvasState(**{"nodes": [{"id": "a", "type": "api_enrich", "position": {"x": 0, "y": 0},
                                     "data": {"api_id": "api1", "parameters": parameters, "param_mapping": {}}}],
                          "edges": []})


def _issues(canvas):
    node = canvas.nodes[0]
    upstream = InferredSchema(columns=[], names_status="indeterminate")
    return ArricchisciNodeValidator().validate_node(node, upstream=upstream)


def test_required_parameter_added_to_the_connection_is_seen():
    stale = _canvas([])
    assert _issues(stale) == []
    live = with_live_api_details(stale, {"api1": {"id": "api1", "parameters": [{"name": "cf", "required": True}],
                                                  "static_parameters": {}}})
    assert live is not stale
    assert any("cf" in (issue.message or "") for issue in _issues(live))
    # Il canvas di partenza (quello salvato) non cambia.
    assert stale.nodes[0].data["parameters"] == []


def test_join_edge_suggestion_survives_the_backend_model():
    """N6: il suggerimento di un join ArcGIS "da completare" non si perde al PUT."""
    suggestion = {"source_column": "id", "target_column": "fk", "label": "relazione r (1:N)"}
    canvas = CanvasState(**{"nodes": [], "edges": [{"id": "e", "source": "a", "target": "b", "join_type": "left",
                                                    "suggestion": suggestion}]})
    assert canvas.model_dump()["edges"][0]["suggestion"] == suggestion


def test_without_connections_the_canvas_is_unchanged():
    canvas = _canvas([{"name": "x"}])
    assert with_live_api_details(canvas, {}) is canvas
    assert with_live_api_details(canvas, {"altra": {"parameters": []}}) is canvas
