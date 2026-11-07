"""UI-2 (06/11/2026, C15): POST /schemas inferisce il grafo una volta.

Prima la preparazione (colonne dell'Output, compute_result_columns) e la
mappa (infer_schemas_response) facevano ognuna l'inferenza da capo. Ora le
due usano la stessa chiamata (SchemaInferenceService.begin_call) e la
preparazione toglie dalle cache solo i nodi che modifica (l'Output e quelli
a valle, forget_downstream).

Contratti:
  1. parita': su un corpus di canvas (Manipola, Estrai, Arricchisci, rami
     scollegati, Output personalizzato, caricamento a valle, nodo guasto) la
     risposta e' identica a quella di prima (preparazione + chiamata nuova);
  2. lavoro: i nodi calcolati per richiesta scendono (mai salgono);
  3. regola delle cache: un nodo modificato e tutto cio' che sta a valle
     escono dalla cache, il resto resta.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.core.projects import router as projects
from app.core.projects import schema_execution_sources
from app.core.projects.canvas_models import CanvasState
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract]

USER = CurrentUser(user_id="u1", username="u", role="admin", group_id="g1")


def _n(node_id, node_type, x=0, y=0, **fields):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, **fields}


def _e(edge_id, source, target, **fields):
    return {"id": edge_id, "source": source, "target": target, **fields}


FILE_A = _n("a", "file_source", columns=[{"name": "id", "type": "int", "id": "col-id"},
                                         {"name": "nome", "type": "str", "id": "col-nome"}])
FILE_B = _n("b", "file_source", y=200, columns=[{"name": "id", "type": "int", "id": "col-id-b"},
                                                {"name": "valore", "type": "real", "id": "col-valore"}])

CORPUS = {
    "semplice": ("manipola", {
        "nodes": [FILE_A, _n("r", "result", 300)],
        "edges": [_e("ar", "a", "r")],
    }),
    "manipola_catena": ("manipola", {
        "nodes": [
            FILE_A, FILE_B,
            _n("j", "multi_join", 200, config={"join_type": "inner", "join_keys": ["id"]}),
            _n("t", "transform", 400, transform_op="rename",
               config={"renames": [{"old_name": "nome", "new_name": "nominativo"}]}),
            _n("f", "transform", 600, transform_op="filter",
               config={"conditions": [{"column": "valore", "operator": ">", "value": 1}]}),
            _n("r", "result", 800, columns=[{"name": "nominativo", "alias": "Chi", "visible": True},
                                            {"name": "valore", "visible": False}]),
            _n("c", "carica", 1000),
        ],
        "edges": [_e("aj", "a", "j", targetHandle="input-0"), _e("bj", "b", "j", targetHandle="input-1"),
                  _e("jt", "j", "t"), _e("tf", "t", "f"), _e("fr", "f", "r"), _e("rc", "r", "c")],
    }),
    "ramo_scollegato": ("manipola", {
        "nodes": [FILE_A, FILE_B, _n("t", "transform", 300, transform_op="filter", config={"conditions": []}),
                  _n("r", "result", 600)],
        "edges": [_e("ar", "a", "r"), _e("bt", "b", "t")],
    }),
    "nodo_guasto": ("manipola", {
        "nodes": [FILE_A, _n("x", "transform", 200, transform_op="custom", config={"code": None}), _n("r", "result", 400)],
        "edges": [_e("ax", "a", "x"), _e("xr", "x", "r")],
    }),
    "estrai_join_group": ("estrai", {
        "nodes": [
            _n("s1", "source", table_name="prima", alias="p",
               columns=[{"name": "id", "type": "int", "id": "s1-id"}, {"name": "valore", "type": "int", "alias": "Totale"}]),
            _n("s2", "source", 0, 200, table_name="seconda", alias="s",
               columns=[{"name": "id", "type": "int"}, {"name": "zona", "type": "str"}]),
            _n("r", "result", 400, group_by_enabled=True, group_by_columns=["zona"],
               aggregations=[{"column": "Totale", "function": "SUM", "alias": "somma"}]),
        ],
        "edges": [_e("j", "s1", "s2", join_type="left", source_column="id", target_column="id"),
                  _e("s2r", "s2", "r")],
    }),
    "arricchisci": ("arricchisci", {
        "nodes": [FILE_A, _n("api", "api_enrich", 300, param_mapping={"q": "{nome}"},
                             output_mapping=[{"path": "x", "column": "meteo"}]),
                  _n("r", "result", 600)],
        "edges": [_e("aa", "a", "api"), _e("ar", "api", "r")],
    }),
}


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_provider", lambda _: "postgres")
    # La visibilita' delle risorse (DB) e' la stessa nei due percorsi e non e'
    # l'oggetto del contratto: senza database risponde «tutto visibile».
    monkeypatch.setattr(projects, "_assert_canvas_refs_visible_for_owner", lambda *a, **k: None)
    kinds = {}
    svc = SimpleNamespace(
        get_project_metadata=lambda pid: SimpleNamespace(id=pid, group_id="g1", project_type=kinds.get(pid, "manipola"),
                                                         default_connection_id=None),
        get_project_canvas=lambda pid: None,
        get_project_output_contract=lambda pid: None,
    )
    monkeypatch.setattr(projects, "_project_service", svc)
    monkeypatch.setattr(projects, "_schema_service", SchemaInferenceService(svc))
    svc.kinds = kinds
    return svc


def _reference(svc, canvas_dict):
    """Il percorso di prima: preparazione, poi una chiamata di inferenza nuova."""
    canvas = CanvasState.model_validate(deepcopy(canvas_dict))
    prepared = projects._prepare_canvas_for_owner("p1", canvas, svc.get_project_metadata("p1"), USER)
    return SchemaInferenceService(svc).infer_schemas_response("p1", canvas=prepared)


def _current(canvas_dict):
    canvas = CanvasState.model_validate(deepcopy(canvas_dict))
    return projects._infer_project_schemas("p1", USER, canvas=canvas)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_parita_con_il_percorso_di_prima(service, name):
    kind, canvas = CORPUS[name]
    service.kinds["p1"] = kind
    assert _current(canvas) == _reference(service, canvas)


@pytest.mark.parametrize("name", sorted(CORPUS))
def test_nodi_calcolati_per_richiesta_non_salgono(service, monkeypatch, name):
    kind, canvas = CORPUS[name]
    service.kinds["p1"] = kind
    original = SchemaInferenceService._infer_node_schema_unchecked
    computed = []

    def counting(self, node, *args, **kwargs):
        if node.id not in self._schema_cache:
            computed.append(node.id)
        return original(self, node, *args, **kwargs)

    monkeypatch.setattr(SchemaInferenceService, "_infer_node_schema_unchecked", counting)
    _reference(service, canvas)
    before = len(computed)
    computed.clear()
    _current(canvas)
    after = len(computed)
    assert after <= before
    has_result = any(n["type"] == "result" for n in canvas["nodes"])
    # Con un Output che legge i suoi ingressi (Manipola, Arricchisci) gli
    # antenati non si ricalcolano piu' per la mappa. In Estrai l'Output e' la
    # proiezione SQL (non visita gli ingressi) e si ricalcola comunque: niente
    # da risparmiare, ma nemmeno lavoro in piu'.
    if has_result and kind != "estrai" and len(canvas["nodes"]) > 2:
        assert after < before


def test_forget_downstream_toglie_il_nodo_e_quelli_a_valle(service):
    kind, canvas_dict = CORPUS["manipola_catena"]
    service.kinds["p1"] = kind
    canvas = CanvasState.model_validate(deepcopy(canvas_dict))
    call = projects._schema_service.begin_call()
    call._infer_all_schemas("p1", canvas)
    assert {"a", "b", "j", "t", "f", "r", "c"} <= set(call._schema_cache)
    call.forget_downstream(canvas, {"t"})
    assert set(call._schema_cache) == {"a", "b", "j"}
    for cache in (call._status_cache, call._types_status_cache, call._order_status_cache):
        assert not ({"t", "f", "r", "c"} & set(cache))
