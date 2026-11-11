"""Ripubblicazione sicura (09/11/2026).

Caso reale in produzione: l'Eseguibile «SC_A_patrimonio_unita_AMM» (v1 del
05/08) ha nel manifest l'input ``cod_ist`` (text, obbligatorio) collegato a
``node_1784712505489 filters.1.value``. Il progetto sorgente attuale non ha
piu' ne' l'input ne' il secondo filtro: l'anteprima diceva
``binding_problems: []`` (controllava solo gli input DEL CANVAS ATTUALE) e la
ripubblicazione avrebbe tolto il filtro per istituto: chi esegue vedrebbe
tutti i dati.

REGOLA UNICA (``service.input_changes``): gli input della versione ATTIVA si
confrontano con quelli della nuova versione; ogni input che sparisce, cambia
tipo, perde un collegamento o lo sposta e' una riga con il motivo. La stessa
regola la usano l'anteprima (``source_status.input_changes``) e la
ripubblicazione (rifiutata, 409, salvo ``confirm_removed_inputs``).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import get_current_user
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.db import EseguibileRow, EseguibileVersionRow
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableInputBinding, ExecutableInputDefinition
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

POS = {"x": 0, "y": 0}
NODE = "node_1784712505489"


def _canvas(filters: int = 2, *, with_input: bool = True, kind: str = "text",
            field: str = "filters.1.value", table: str = "patrimonio") -> dict:
    nodes = [
        {"id": NODE, "type": "source", "position": POS, "table_name": table,
         "columns": [{"name": "cod_ist", "type": "str", "visible": True}],
         "filters": [{"column": "cod_ist", "operator": "=", "value": ""} for _ in range(filters)]},
        {"id": "out", "type": "result", "position": POS},
    ]
    edges = [{"id": "e", "source": NODE, "target": "out", "join_type": "output"}]
    if with_input:
        nodes.append({"id": "in", "type": "project_input", "position": POS, "data": {
            "input_key": "cod_ist", "label": "Codice istituto", "input_kind": kind, "required": True,
            "bindings": [{"target_node_id": NODE, "target_field": field}]}})
        edges.append({"id": "b", "source": "in", "target": NODE})
    return {"nodes": nodes, "edges": edges}


def _manifest(canvas: dict) -> dict:
    return compile_manifest(source_project_id="p1", project_type="estrai", canvas=canvas,
                            default_connection_id="conn").model_dump(mode="json")


PUBLISHED = _canvas(filters=2)
# Il progetto sorgente attuale del caso reale: niente input, un solo filtro.
REAL_CURRENT = _canvas(filters=1, with_input=False)


def _version(canvas: dict) -> EseguibileVersionRow:
    return EseguibileVersionRow(
        id="exec_37045e4c1d8f_v1", eseguibile_id="exec_37045e4c1d8f", version_number=1, schema_version=2,
        project_type="estrai", source_project_id="p1", source_hash=eseguibili_db.canvas_fingerprint(canvas),
        canvas=copy.deepcopy(canvas), manifest=_manifest(canvas), status="published",
        created_by="u1", created_at="2026-08-05T08:35:50+00:00",
    )


def _record() -> EseguibileRow:
    return EseguibileRow(
        id="exec_37045e4c1d8f", name="SC_A_patrimonio_unita_AMM", description="", canvas=copy.deepcopy(PUBLISHED),
        expected_columns=[], default_output_format="xlsx", group_id="g1", created_by="u1",
        created_at="2026-08-05T08:35:50+00:00", updated_at="2026-08-05T08:35:50+00:00",
        source_canvas_id="p1", project_type="estrai", active_version_id="exec_37045e4c1d8f_v1",
    )


def _project(canvas: dict) -> SimpleNamespace:
    return SimpleNamespace(project_id="p1", group_id="g1", project_type="estrai",
                           canvas=copy.deepcopy(canvas), default_connection_id="conn")


def _status(current: dict, published: dict = PUBLISHED) -> dict:
    with patch.object(eseguibili_db, "get_eseguibile_version", return_value=_version(published)), \
         patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                      return_value=_project(current)):
        return eseguibili_service.source_status(_record())


class _Publish:
    """publish_new_version con il DB finto: registra le versioni create."""

    def __init__(self, current: dict):
        self.current = current
        self.created: list[dict] = []

    def __call__(self, **kwargs):
        return self.through(
            lambda: eseguibili_service.publish_new_version(eseguibile=_record(), created_by="u1", **kwargs))

    def through(self, action):
        with patch.object(eseguibili_db, "get_eseguibile_version", return_value=_version(PUBLISHED)), \
             patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                          return_value=_project(self.current)), \
             patch.object(eseguibili_service.connection_sets, "connection_set_options", return_value=[]), \
             patch.object(eseguibili_db, "create_eseguibile_version",
                          side_effect=lambda **kw: self.created.append(kw) or _version(self.current)), \
             patch.object(eseguibili_db, "activate_eseguibile_version", return_value=None), \
             patch.object(eseguibili_db, "get_eseguibile", return_value=_record()):
            return action()


# ── Caso reale ──────────────────────────────────────────────────────────────

def test_real_case_removed_input_is_listed_with_explicit_reason():
    status = _status(REAL_CURRENT)
    assert status["state"] == "changed"
    # Prima: nessun problema (si guardavano solo gli input del canvas attuale).
    assert status["binding_problems"] == []
    [change] = status["input_changes"]
    assert (change["input_key"], change["change"]) == ("cod_ist", "removed")
    assert change["motivo"].startswith("L'input «cod_ist» non c'è più nel progetto: chi esegue vedrebbe i dati senza quel filtro")
    assert f"{NODE}.filters.1.value" in change["motivo"]


def test_real_case_republish_is_refused_without_confirmation():
    publish = _Publish(REAL_CURRENT)
    with pytest.raises(eseguibili_service.InputChangesNotConfirmed) as refused:
        publish()
    assert "L'input «cod_ist» non c'è più nel progetto" in str(refused.value)
    assert [c["input_key"] for c in refused.value.changes] == ["cod_ist"]
    assert publish.created == []


def test_real_case_republish_is_accepted_with_explicit_confirmation():
    publish = _Publish(REAL_CURRENT)
    publish(confirm_removed_inputs=True)
    assert len(publish.created) == 1
    assert publish.created[0]["manifest"]["inputs"] == []


def test_real_case_endpoint_409_without_flag_200_with_flag():
    from app.core.eseguibili import router as eseguibili_router
    app = FastAPI()
    app.include_router(eseguibili_router.router)
    app.dependency_overrides[get_current_user] = lambda: build_current_user(role="admin", group_id="g1", user_id="u1")
    client = TestClient(app)
    publish = _Publish(REAL_CURRENT)
    url = "/eseguibili/exec_37045e4c1d8f/versions"
    with patch.object(eseguibili_router, "_check_group_access", lambda *a: None), \
         patch.object(eseguibili_router, "_details_response", lambda record, user: {
             "id": record.id, "name": record.name, "expected_columns": [], "default_output_format": "xlsx",
             "group_id": "g1", "created_by": "u1", "created_at": "t", "updated_at": "t"}):
        refused = publish.through(lambda: client.post(url))
        refused_false = publish.through(lambda: client.post(url, json={"confirm_removed_inputs": False}))
        assert publish.created == []
        accepted = publish.through(lambda: client.post(url, json={"confirm_removed_inputs": True}))
    assert refused.status_code == 409, refused.text
    assert "cod_ist" in refused.text
    assert refused_false.status_code == 409
    assert accepted.status_code == 200, accepted.text
    assert len(publish.created) == 1


# ── Le altre righe della regola ─────────────────────────────────────────────

def test_unchanged_input_gives_no_row_and_republish_is_free():
    current = _canvas(filters=2, table="patrimonio_2026")
    status = _status(current)
    assert status["state"] == "changed"
    assert status["input_changes"] == [] and status["binding_problems"] == []
    publish = _Publish(current)
    publish()
    assert len(publish.created) == 1


def test_kind_changed_from_current_user_to_text_is_a_row():
    published = _canvas(filters=2, kind="current_user")
    status = _status(_canvas(filters=2, kind="text"), published=published)
    [change] = status["input_changes"]
    assert change["change"] == "kind_changed"
    assert "da current_user a text" in change["motivo"]
    assert "non lo metterebbe più il server" in change["motivo"]


def test_target_changed_is_a_row():
    status = _status(_canvas(filters=2, field="filters.0.value"))
    [change] = status["input_changes"]
    assert (change["change"], change["target_field"]) == ("target_changed", "filters.1.value")
    assert f"ma a {NODE}.filters.0.value" in change["motivo"]


def test_binding_lost_and_tolerant_old_manifests():
    published = {
        "inputs": [{"key": "cod_ist", "kind": "text"}],
        "bindings": [{"input_key": "cod_ist", "target_node_id": NODE, "mode": "node_field",
                      "target_field": "filters.1.value"}],
    }
    same_input = [ExecutableInputDefinition(key="cod_ist", label="x", kind="text")]
    [change] = eseguibili_service.input_changes(published, same_input, [])
    assert change["change"] == "binding_lost"
    still = [ExecutableInputBinding(input_key="cod_ist", target_node_id=NODE, mode="node_field",
                                    target_field="filters.1.value")]
    assert eseguibili_service.input_changes(published, same_input, still) == []
    # Manifest vuoto o illeggibile della versione attiva: nessuna riga.
    assert eseguibili_service.input_changes({}, [], []) == []
    assert eseguibili_service.input_changes(None, [], []) == []


def test_preview_and_republish_use_the_same_rule():
    import inspect
    assert "input_changes(published_manifest" in inspect.getsource(eseguibili_service.republish_preview)
    assert "input_changes(published, manifest.inputs" in inspect.getsource(eseguibili_service.publish_new_version)
    # «Utente che esegue» a monte tolto: stessa regola in ripubblicazione e N1
    # (correzioni minori A6: la ripubblicazione e l'anteprima confrontano lo
    # snapshot con la chiusura del canvas VIVO, l'avvio e N1 con quella
    # della versione pubblicata).
    assert "upstream_identity_changes(_republish_removed(eseguibile, closure))" in inspect.getsource(
        eseguibili_service.publish_new_version)
    assert "republish_removed_inputs(" in inspect.getsource(eseguibili_service._republish_removed)
    assert "republish_removed_inputs(" in inspect.getsource(eseguibili_service._republish_preview_rows)
    status_src = inspect.getsource(eseguibili_service.source_status)
    assert "_launch_removed(version, eseguibile.group_id)" in status_src
    assert "_republish_preview_rows(version, canvas, proj, eseguibile.group_id)" in status_src
