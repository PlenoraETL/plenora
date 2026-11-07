"""Contratti HTTP per inferire i canvas non salvati senza persistenza."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import get_current_user
from app.core.projects import router as projects
from app.core.projects.canvas_models import CanvasState
from app.core.projects.schema_inference import SchemaInferenceService
from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def context(monkeypatch):
    payload = {
        "nodes": [
            {"id": "source", "type": "file_source", "position": {"x": 0, "y": 0},
             "columns": [{"name": "original", "type": "str", "id": "col-original"}]},
            {"id": "result", "type": "result", "position": {"x": 100, "y": 0}},
        ],
        "edges": [{"id": "edge", "source": "source", "target": "result"}],
    }
    service = MagicMock()
    service.get_project_metadata.return_value = SimpleNamespace(id="p1", group_id="g1")
    service.get_project_canvas.return_value = CanvasState.model_validate(payload)
    monkeypatch.setattr(projects, "_project_service", service)
    monkeypatch.setattr(projects, "_schema_service", SchemaInferenceService(service))
    app = FastAPI()
    app.include_router(projects.router)
    with TestClient(app) as client:
        yield app, client, service, payload


def authenticate(app, role="viewer", group_id="g1"):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u1", username="reader", role=role, group_id=group_id,
    )


def test_saved_canvas_parity_unsaved_changes_and_no_persistence(context):
    app, client, service, payload = context
    authenticate(app)
    original_canvas = service.get_project_canvas.return_value.model_dump()
    original = client.get("/projects/p1/schemas")
    assert original.status_code == 200
    same = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert same.status_code == 200
    assert same.json() == original.json()
    for node_id in ("source", "result"):
        state = same.json()["schema_states"][node_id]
        assert {"names_status", "types_status", "order_status", "reason"} <= state.keys()
        assert state["names_status"] == "certain"
        assert all({"id", "name", "type", "source_name", "optional"} <= col.keys()
                   for col in same.json()["schemas"][node_id])
    changed = deepcopy(payload)
    changed["nodes"][0]["columns"].append(
        {"name": "unsaved", "type": "int", "id": "col-unsaved"}
    )
    service.reset_mock()
    inferred = client.post("/projects/p1/schemas", json={"canvas": changed})
    assert inferred.status_code == 200
    for node_id in ("source", "result"):
        assert [c["name"] for c in inferred.json()["schemas"][node_id]] == ["original", "unsaved"]
    # La preparazione legge i contratti, senza caricare o salvare il canvas.
    service.get_project_canvas.assert_not_called()
    service.update_canvas.assert_not_called()
    assert service.get_project_canvas.return_value.model_dump() == original_canvas
    assert client.get("/projects/p1/schemas").json() == original.json()


def test_empty_unsaved_canvas_does_not_fall_back_to_saved(context):
    app, client, service, _ = context
    authenticate(app)
    response = client.post("/projects/p1/schemas", json={"canvas": {"nodes": [], "edges": []}})
    assert response.status_code == 200
    assert response.json() == {"schemas": {}, "schema_states": {}}
    service.get_project_canvas.assert_not_called()


@pytest.mark.parametrize("role,group_id,status", [
    ("viewer", "g1", 200), ("user", "g1", 200), ("admin", "g1", 200),
    ("superadmin", "other", 200), ("viewer", "other", 403),
    ("user", "other", 403), ("esecutore", "g1", 403),
    (None, None, 401),
])
def test_permission_parity(context, role, group_id, status):
    app, client, service, payload = context
    if role:
        authenticate(app, role, group_id)
    get = client.get("/projects/p1/schemas")
    post = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert get.status_code == post.status_code == status
    assert get.json() == post.json()
    if status != 200:
        service.get_project_canvas.assert_not_called()


def test_missing_project_parity(context):
    app, client, service, payload = context
    authenticate(app)
    service.get_project_metadata.return_value = None
    get = client.get("/projects/missing/schemas")
    post = client.post("/projects/missing/schemas", json={"canvas": payload})
    assert get.status_code == post.status_code == 404
    assert get.json() == post.json()


@pytest.mark.parametrize("body", [{}, {"canvas": None}, {"canvas": {"nodes": [{}]}},
                                      {"canvas": {"edges": [{}]}}])
def test_invalid_canvas_is_rejected(context, body):
    app, client, service, _ = context
    authenticate(app)
    assert client.post("/projects/p1/schemas", json=body).status_code == 422
    service.get_project_canvas.assert_not_called()


@pytest.mark.parametrize("resource", ["projects", "file_sources", "connections", "datasources", "arcgis_connections"])
def test_visibilita_identica_a_put_prima_della_sincronizzazione(context, monkeypatch, resource):
    app, client, service, payload = context
    authenticate(app, "user")
    monkeypatch.setattr(projects, "_collect_canvas_resource_refs", lambda _: {resource: {"altro-gruppo"}})
    monkeypatch.setattr(projects._canvas_access, "find_inaccessible_canvas_refs", lambda *a, **kw: {key: ({"altro-gruppo"} if key == resource else set()) for key in ("projects", "file_sources", "connections", "datasources", "arcgis_connections")})
    get = client.put("/projects/p1/canvas", json=payload)
    post = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert get.status_code == post.status_code == 400
    assert get.json() == post.json()
    service.get_project_output_contract.assert_not_called()
    service.update_canvas.assert_not_called()


@pytest.mark.parametrize("nodes,edges", [(501, 0), (0, 2001)])
def test_limite_canvas(context, nodes, edges):
    app, client, service, payload = context
    authenticate(app)
    canvas = {"nodes": [dict(payload["nodes"][0], id=f"n{i}") for i in range(nodes)],
              "edges": [dict(payload["edges"][0], id=f"e{i}") for i in range(edges)]}
    response = client.post("/projects/p1/schemas", json={"canvas": canvas})
    assert response.status_code == 422
    assert "Il canvas supera il limite" in response.text
    service.get_project_metadata.assert_not_called()


def test_rinomina_sorgente_stesse_colonne_del_put(context, monkeypatch):
    app, client, service, payload = context
    authenticate(app, "user")
    payload["nodes"][0].update(type="project_source", source_project_id="upstream")
    monkeypatch.setattr(projects, "_visible_project_ids_strict", lambda *a, **kw: {"upstream"})
    contract = {"columns": [{"id": "col-original", "physical_name": "rinominata", "type": {"logical": "string"}}]}
    service.get_project_output_contract.side_effect = lambda pid: contract if pid == "upstream" else None
    monkeypatch.setattr("app.core.projects.schema_inference.load_project_source_schema_context",
                        lambda _: SimpleNamespace(output_contract=contract))
    post = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert post.status_code == 200, post.text
    service.update_canvas.assert_not_called()
    put = client.put("/projects/p1/canvas", json=payload)
    assert put.status_code == 200, put.text
    saved = service.update_canvas.call_args.args[1]
    assert [col.name for col in saved.nodes[0].columns] == ["rinominata"]
    assert post.json()["schemas"] == SchemaInferenceService(service).infer_schemas_response("p1", saved)["schemas"]



def test_inferenze_concorrenti_non_condividono_cache(context, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    _, _, service, payload = context
    shared = SchemaInferenceService(service)
    first_cached, second_done = Event(), Event()
    original = SchemaInferenceService._infer_node_schema

    def interleave(self, **kwargs):
        result = original(self, **kwargs)
        if kwargs["project_id"] == "prima" and kwargs["node"].id == "source":
            first_cached.set()
            assert second_done.wait(10)
        return result

    monkeypatch.setattr(SchemaInferenceService, "_infer_node_schema", interleave)
    first = CanvasState.model_validate(payload)
    changed = deepcopy(payload)
    changed["nodes"][0]["columns"][0]["name"] = "seconda"
    second = CanvasState.model_validate(changed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(shared.infer_all_schemas, "prima", first)
        assert first_cached.wait(10)
        try:
            result_second = shared.infer_all_schemas("seconda", second)
        finally:
            second_done.set()
        result_first = pending.result(timeout=10)
    assert [c["name"] for c in result_first["result"]] == ["original"]
    assert [c["name"] for c in result_second["result"]] == ["seconda"]



def test_project_source_altro_gruppo_rifiutato_da_put_e_post(context, monkeypatch):
    app, client, service, payload = context
    authenticate(app, "user")
    payload["nodes"][0].update(type="project_source", source_project_id="altro-gruppo")
    visibility = MagicMock(return_value=set())
    monkeypatch.setattr(projects, "_visible_project_ids_strict", visibility)
    put = client.put("/projects/p1/canvas", json=payload)
    post = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert put.status_code == post.status_code == 400
    assert put.json() == post.json()
    assert "project_source: altro-gruppo" in post.json()["detail"]
    assert visibility.call_count == 2
    visibility.assert_called_with({"altro-gruppo"}, "g1", False)
    service.get_project_output_contract.assert_not_called()
    service.update_canvas.assert_not_called()


def test_passo_senza_ingresso_non_blocca_salvataggio_ne_schemi(context):
    """Collaudo live L5: un passo senza ingresso (sorgente eliminata) non
    blocca il salvataggio; lo segnala Valida (structure.orphan). Prima il
    PUT rispondeva 400 «Canvas contiene nodi orfani ...: <id>» e il lavoro
    restava non salvato."""
    app, client, service, payload = context
    authenticate(app, "user")
    payload["edges"] = []
    service.update_canvas.return_value = None
    put = client.put("/projects/p1/canvas", json=payload)
    assert "orfani" not in put.text
    assert put.status_code != 400, put.text
    service.update_canvas.assert_called_once()
    post = client.post("/projects/p1/schemas", json={"canvas": payload})
    assert post.status_code == 200
    assert [c["name"] for c in post.json()["schemas"]["source"]] == ["original"]
