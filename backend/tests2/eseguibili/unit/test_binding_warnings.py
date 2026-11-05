"""Avviso sugli Eseguibili pubblicati con binding su bersagli non piu' letti.

REGOLA: un'unica funzione (``eseguibili.manifest.check_binding``) verifica i
binding contro le capability correnti
(``binding_capabilities.NODE_TARGET_FAMILIES``). La pubblicazione la usa per
rifiutare; l'elenco e il dettaglio degli Eseguibili la usano sul manifest
attivo e sul canvas della versione per esporre ``binding_warnings``
(l'esecuzione resta com'e': l'input parte e il valore e' ignorato).
"""

from __future__ import annotations

import copy
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import get_current_user
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.db import EseguibileRow, EseguibileSummaryRow
from app.core.eseguibili.manifest import compile_manifest
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

POS = {"x": 0, "y": 0}


def _canvas(field: str = "filters.0.value", operator: str = "=") -> dict:
    return {
        "nodes": [
            {"id": "src", "type": "source", "position": POS, "table_name": "pratiche",
             "columns": [{"name": "owner", "type": "str", "visible": True}],
             "filters": [{"column": "owner", "operator": operator, "value": ""}]},
            {"id": "out", "type": "result", "position": POS},
            {"id": "in", "type": "project_input", "position": POS, "data": {
                "input_key": "x", "label": "X", "input_kind": "text", "required": False,
                "bindings": [{"target_node_id": "src", "target_field": field}]}},
        ],
        "edges": [{"id": "e", "source": "src", "target": "out", "join_type": "output"},
                  {"id": "b", "source": "in", "target": "src"}],
    }


def _published(canvas: dict) -> dict:
    return compile_manifest(source_project_id="p", project_type="estrai", canvas=canvas,
                            default_connection_id="conn").model_dump(mode="json")


def _with_binding(manifest: dict, **changes) -> dict:
    """Manifest pubblicato in passato con un binding che oggi non si
    accetterebbe piu' (la pubblicazione di allora lo ha congelato)."""
    manifest = copy.deepcopy(manifest)
    manifest["bindings"][0].update(changes)
    return manifest


def test_valid_binding_has_no_warning():
    canvas = _canvas()
    manifest = _published(canvas)
    assert manifest["bindings"][0]["target_field"] == "filters.0.value"
    assert eseguibili_service.binding_warnings(manifest["bindings"], canvas) == []


@pytest.mark.parametrize("case", ["family_removed", "operator_without_value", "node_missing"])
def test_binding_on_a_target_no_longer_read_is_warned(case):
    canvas = _canvas()
    manifest = _published(canvas)
    if case == "family_removed":
        # ``value`` al primo livello: famiglia offerta prima della regola
        # «ogni bersaglio offerto e' letto», nessun esecutore la legge.
        manifest = _with_binding(manifest, target_field="value")
    elif case == "operator_without_value":
        canvas = _canvas(operator="IS NULL")
    else:
        manifest = _with_binding(manifest, target_node_id="sparito")

    warnings = eseguibili_service.binding_warnings(manifest["bindings"], canvas)

    assert len(warnings) == 1
    warning = warnings[0]
    assert warning["input_key"] == "x"
    assert set(warning) == {"input_key", "target_node_id", "target_field", "motivo"}
    assert warning["motivo"]


def test_publication_and_warning_share_the_rule():
    """Lo stesso bersaglio: la pubblicazione rifiuta con il motivo che
    l'avviso mostra sugli Eseguibili gia' pubblicati."""
    canvas = _canvas()
    stale = _with_binding(_published(canvas), target_field="value")
    motivo = eseguibili_service.binding_warnings(stale["bindings"], canvas)[0]["motivo"]
    with pytest.raises(ValueError) as exc:
        _published(_canvas(field="value"))
    assert str(exc.value) == motivo


def test_unreadable_bindings_produce_no_warning():
    assert eseguibili_service.binding_warnings(None, _canvas()) == []
    assert eseguibili_service.binding_warnings([{"mode": "boh"}], _canvas()) == []


# ---------------------------------------------------------------- API


def _client():
    from app.core.eseguibili import router as router_module

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user(role="admin", group_id="g1")
    app.include_router(router_module.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), router_module


def _target_nodes(canvas: dict, manifest: dict) -> tuple:
    targets = {b["target_node_id"] for b in manifest["bindings"]}
    return tuple(n for n in canvas["nodes"] if n["id"] in targets)


@pytest.mark.parametrize("stale", [True, False])
def test_list_and_detail_expose_binding_warnings(stale):
    canvas = _canvas()
    manifest = _published(canvas)
    if stale:
        manifest = _with_binding(manifest, target_field="value")
    common = dict(id="e1", name="Es", description="", default_output_format="csv", group_id="g1",
                  created_by="u", created_at="2026-11-05T00:00:00", updated_at="2026-11-05T00:00:00",
                  project_type="estrai")
    summary = EseguibileSummaryRow(**common, bindings=tuple(manifest["bindings"]),
                                   binding_target_nodes=_target_nodes(canvas, manifest))
    detail = EseguibileRow(**common, canvas=canvas, expected_columns=[], manifest=manifest,
                           active_version_id="v1")
    client, router_module = _client()
    with patch.object(router_module.eseguibili_db, "list_eseguibili", return_value=[summary]), \
         patch.object(router_module.eseguibili_db, "get_eseguibile", return_value=detail), \
         patch.object(router_module, "visible_resource_ids", return_value={"e1"}), \
         patch.object(router_module, "assert_resource_visible", lambda *a, **kw: None), \
         patch.object(router_module, "compute_can_edit", return_value=True):
        listed = client.get("/api/eseguibili")
        shown = client.get("/api/eseguibili/e1")

    assert listed.status_code == 200, listed.text
    assert shown.status_code == 200, shown.text
    for warnings in (listed.json()[0]["binding_warnings"], shown.json()["binding_warnings"]):
        if stale:
            assert [(w["input_key"], w["target_node_id"], w["target_field"]) for w in warnings] == [
                ("x", "src", "value")]
        else:
            assert warnings == []
