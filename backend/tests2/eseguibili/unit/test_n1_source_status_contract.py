"""N1 (09/11/2026): un Eseguibile pubblicato che non corrisponde piu' al suo
progetto si vede.

Caso reale: l'Eseguibile «SC_A_patrimonio_unita_AMM» (v1) ha il binding
``cod_ist -> node_1784712505489 filters.1.value``; nel progetto sorgente
attuale quel nodo ha un solo filtro. L'Eseguibile funziona con la sua copia
congelata (versione = release, invariato), ma nulla lo diceva e la
ripubblicazione falliva al primo binding rotto.

REGOLA UNICA (P2, 09/11/2026): la copia congelata della versione attiva e il
canvas attuale del progetto sono uguali PER L'ESECUZIONE
(``service.source_changed``: posizioni, dimensioni, selezione, campi «_»,
note, id e doppioni degli archi, ordine e 1/1.0 non contano). Se cambiata,
l'anteprima della ripubblicazione elenca TUTTI i binding che non troverebbero
il bersaglio (la regola della pubblicazione, ``manifest.check_binding``).
La lista lo calcola solo per le righe che l'utente puo' ripubblicare (P5, P7:
``compute_can_edit``, il predicato del pulsante), con una query per i canvas.
"""

from __future__ import annotations

import copy
import inspect
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.dependencies import get_current_user
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.db import EseguibileRow, EseguibileVersionRow
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

POS = {"x": 0, "y": 0}
NODE = "node_1784712505489"


def _canvas(filters: int = 2, *, second_input: bool = False) -> dict:
    nodes = [
        {"id": NODE, "type": "source", "position": POS, "table_name": "patrimonio",
         "columns": [{"name": "cod_ist", "type": "str", "visible": True}],
         "filters": [{"column": "cod_ist", "operator": "=", "value": ""} for _ in range(filters)]},
        {"id": "out", "type": "result", "position": POS},
        {"id": "in", "type": "project_input", "position": POS, "data": {
            "input_key": "cod_ist", "label": "Codice istituto", "input_kind": "text", "required": False,
            "bindings": [{"target_node_id": NODE, "target_field": "filters.1.value"}]}},
    ]
    edges = [{"id": "e", "source": NODE, "target": "out", "join_type": "output"},
             {"id": "b", "source": "in", "target": NODE}]
    if second_input:
        nodes.append({"id": "in2", "type": "project_input", "position": POS, "data": {
            "input_key": "anno", "label": "Anno", "input_kind": "text", "required": False,
            "bindings": [{"target_node_id": "sparito", "target_field": "filters.0.value"}]}})
    return {"nodes": nodes, "edges": edges}


def _version(canvas: dict, source_hash: str | None = None) -> EseguibileVersionRow:
    return EseguibileVersionRow(
        id="exec_1_v1", eseguibile_id="exec_1", version_number=1, schema_version=2,
        project_type="estrai", source_project_id="p1",
        source_hash=source_hash or eseguibili_db.canvas_fingerprint(canvas),
        canvas=copy.deepcopy(canvas), manifest={}, status="published",
        created_by="u1", created_at="2026-08-05T08:35:50+00:00",
    )


def _record(canvas: dict) -> EseguibileRow:
    return EseguibileRow(
        id="exec_1", name="SC_A_patrimonio_unita_AMM", description="", canvas=copy.deepcopy(canvas),
        expected_columns=[], default_output_format="xlsx", group_id="g1", created_by="u1",
        created_at="2026-08-05T08:35:50+00:00", updated_at="2026-08-05T08:35:50+00:00",
        source_canvas_id="p1", project_type="estrai", active_version_id="exec_1_v1",
    )


def _project(canvas: object) -> SimpleNamespace:
    return SimpleNamespace(project_id="p1", group_id="g1", project_type="estrai",
                           canvas=canvas, default_connection_id="conn")


def _status(published: dict, current: object, *, source_hash: str | None = None) -> dict:
    with patch.object(eseguibili_db, "get_eseguibile_version", return_value=_version(published, source_hash)), \
         patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                      return_value=_project(current) if current is not None else None):
        return eseguibili_service.source_status(_record(published))


def test_same_canvas_same_fingerprint_no_warning():
    canvas = _canvas()
    status = _status(canvas, copy.deepcopy(canvas))
    assert (status["state"], status["changed"], status["binding_problems"]) == ("same", False, [])
    # Anche con la colonna storica TEXT e l'ordine delle chiavi diverso.
    import json
    shuffled = json.dumps({"edges": canvas["edges"], "nodes": canvas["nodes"]})
    assert _status(canvas, shuffled)["state"] == "same"


def test_changed_canvas_is_reported_with_version_and_date():
    published = _canvas()
    current = copy.deepcopy(published)
    current["nodes"][0]["table_name"] = "patrimonio_2026"
    status = _status(published, current)
    assert status["state"] == "changed" and status["changed"] is True
    assert (status["version"], status["published_at"], status["name"]) == (
        1, "2026-08-05T08:35:50+00:00", "SC_A_patrimonio_unita_AMM")
    assert status["binding_problems"] == [] and status["publish_error"] is None


def test_real_case_broken_binding_is_listed_with_reason():
    status = _status(_canvas(filters=2), _canvas(filters=1))
    assert status["state"] == "changed"
    [problem] = status["binding_problems"]
    assert (problem["input_key"], problem["target_node_id"], problem["target_field"]) == (
        "cod_ist", NODE, "filters.1.value")
    assert problem["motivo"]


def test_every_broken_binding_is_listed_not_only_the_first():
    status = _status(_canvas(filters=2), _canvas(filters=1, second_input=True))
    assert [p["input_key"] for p in status["binding_problems"]] == ["cod_ist", "anno"]
    # La pubblicazione, invece, si ferma al primo: e' la stessa regola.
    from app.core.eseguibili.manifest import compile_manifest
    with pytest.raises(ValueError):
        compile_manifest(source_project_id="p1", project_type="estrai",
                         canvas=_canvas(filters=1, second_input=True), default_connection_id="conn")


def test_missing_source_project_is_declared_not_an_error():
    status = _status(_canvas(), None)
    assert (status["state"], status["changed"]) == ("no_source", None)


def test_backfilled_md5_versions_compare_on_their_frozen_canvas():
    canvas = _canvas()
    assert _status(canvas, copy.deepcopy(canvas), source_hash="0" * 32)["state"] == "same"


def test_publication_and_comparison_use_the_same_fingerprint():
    source = inspect.getsource(eseguibili_db.create_eseguibile_version)
    assert "canvas_fingerprint(canvas)" in source and "hashlib" not in source


def test_p2_layout_and_automatic_saves_are_not_a_change():
    """Solo spostamenti, misure, selezione, campi «_», note, collegamenti doppi
    (pendingSync), ordine e normalizzazione del modello: nessun avviso."""
    published = _canvas()
    current = copy.deepcopy(published)
    for i, node in enumerate(current["nodes"]):
        node["position"] = {"x": 100 * i, "y": 7.5}
        node.update(width=240, height=88, selected=True, dragging=False,
                    measured={"width": 240, "height": 88}, style={"border": "1px"})
        node.setdefault("data", {})["_advOpen"] = True
    current["nodes"].reverse()
    current["nodes"].append({"id": "nota", "type": "comment", "position": POS, "text": "promemoria"})
    current["edges"] = list(reversed(current["edges"])) + [{**current["edges"][0], "id": "doppio"}]
    # Normalizzazione di CanvasState: valori predefiniti scritti o assenti,
    # alias del tipo, 1 e 1.0.
    current["nodes"][-2]["write_mode"] = "create"
    published["nodes"][1]["type"] = "resultNode"
    published["nodes"][1]["limit"] = 10
    current_out = next(n for n in current["nodes"] if n["id"] == "out")
    current_out["limit"] = 10.0
    assert eseguibili_service.source_changed(published, current) is False
    assert _status(published, current)["state"] == "same"


def test_p2_a_changed_filter_is_a_change():
    published = _canvas()
    current = copy.deepcopy(published)
    current["nodes"][0]["filters"][0]["value"] = "A1"
    assert eseguibili_service.source_changed(published, current) is True
    assert _status(published, current)["state"] == "changed"
    # Un canvas illeggibile: non si sa (nessun avviso), non «cambiato».
    assert eseguibili_service.source_changed(published, "{non json") is None


def test_p2_one_rule_for_list_detail_editor_and_home():
    """Dettaglio ed editor (source_status), lista e home (source_changed_by_id
    -> campo source_changed) passano dalla stessa funzione; l'impronta
    sha256 resta solo il source_hash della pubblicazione."""
    status_src = inspect.getsource(eseguibili_service.source_status)
    by_id_src = inspect.getsource(eseguibili_service.source_changed_by_id)
    assert "source_changed(version.canvas, canvas)" in status_src
    # La lista passa dall'involucro che isola una riga non confrontabile (D2),
    # che a sua volta usa la stessa funzione; e (A7) dall'avvio fermo per un
    # input a monte tolto, la stessa funzione del dettaglio.
    assert "_source_or_upstream_changed(row)" in by_id_src
    row_src = inspect.getsource(eseguibili_service._source_or_upstream_changed)
    assert "_source_changed_or_unknown(row.version_canvas, row.project_canvas)" in row_src
    assert "_launch_removed(version" in row_src
    assert "_launch_removed(version, eseguibile.group_id)" in status_src
    assert "source_changed(version_canvas, project_canvas)" in inspect.getsource(
        eseguibili_service._source_changed_or_unknown
    )
    assert "canvas_fingerprint" not in inspect.getsource(eseguibili_service)
    assert not hasattr(eseguibili_db, "SOURCE_CHANGED_SQL")


def _summary_row(**over):
    return eseguibili_db.eseguibile_summary_row_from_db({
        "id": "e", "name": "n", "default_output_format": "xlsx", "group_id": "g1", "created_by": "u1",
        "created_at": "t", "updated_at": "t", **over})


@pytest.mark.parametrize("role,group,reads", [
    ("esecutore", "g1", []), ("user", "g1", []), ("admin", "g3", []),
    ("admin", "g1", ["mine"]), ("superadmin", "g9", ["mine", "shared"]),
])
def test_p5_p7_list_reads_canvases_only_for_rows_the_user_can_republish(role, group, reads):
    """P5: i canvas si leggono (una query) solo per le righe ripubblicabili;
    P7: il campo c'e' solo dove c'e' il pulsante (compute_can_edit), non su
    un Eseguibile condiviso da un altro gruppo."""
    from app.core.eseguibili import router as eseguibili_router
    rows = [_summary_row(id="mine"), _summary_row(id="shared", group_id="g2", created_by="u2")]
    asked: list[list[str]] = []

    def fake_canvases(ids):
        asked.append(list(ids))
        return [eseguibili_db.EseguibileSourceCanvasesRow(id=i, version_canvas=_canvas(2), project_canvas=_canvas(1))
                for i in ids]

    user = build_current_user(role=role, group_id=group, user_id="u1")
    with patch.object(eseguibili_db, "list_eseguibili", return_value=rows), \
         patch.object(eseguibili_router, "visible_resource_ids", return_value={"mine", "shared"}), \
         patch.object(eseguibili_db, "list_source_canvases", side_effect=fake_canvases), \
         patch("app.core.auth.ownership._users_dal.get_user", return_value=None):
        out = eseguibili_router.list_eseguibili(limit=200, offset=0, current_user=user)
    assert asked == ([reads] if reads else [])
    assert {r["id"]: r["source_changed"] for r in out} == {
        "mine": True if "mine" in reads else None, "shared": True if "shared" in reads else None}


def test_p5_list_query_has_no_canvas_comparison():
    captured: list[str] = []
    with patch.object(eseguibili_db, "_fetchall", lambda sql, *a, **k: captured.append(sql) or []):
        eseguibili_db.list_eseguibili(group_id="g1")
        eseguibili_db.list_source_canvases(["a", "b"])
    assert "p.canvas" not in captured[0] and "JOIN projects" not in captured[0]
    assert "v.canvas AS version_canvas, p.canvas AS project_canvas" in captured[1]
    assert "WHERE e.id IN (%s, %s)" in captured[1]


def _client(role: str = "admin") -> TestClient:
    from app.core.eseguibili.router import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: build_current_user(role=role, group_id="g1", user_id="u1")
    return TestClient(app)


def test_endpoints_are_admin_only_and_carry_the_preview():
    published, current = _canvas(filters=2), _canvas(filters=1)
    with patch.object(eseguibili_db, "get_eseguibile", return_value=_record(published)), \
         patch.object(eseguibili_db, "get_eseguibile_version", return_value=_version(published)), \
         patch.object(eseguibili_db, "list_eseguibili_by_source",
                      return_value=[eseguibili_db.EseguibileSourceRefRow(id="exec_1", name="x")]), \
         patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                      return_value=_project(current)), \
         patch("app.core.eseguibili.router._check_group_access", lambda *a: None):
        detail = _client().get("/eseguibili/exec_1/source-status")
        listed = _client().get("/eseguibili/by-source/p1")
        assert _client(role="user").get("/eseguibili/by-source/p1").status_code == 403
    assert detail.status_code == 200, detail.text
    assert detail.json()["binding_problems"][0]["target_field"] == "filters.1.value"
    assert listed.status_code == 200 and listed.json()[0]["state"] == "changed"


def test_p7_editor_notice_lists_only_what_the_user_can_republish():
    """GET /by-source: stesso predicato della lista (compute_can_edit), non
    solo il gruppo: un Eseguibile del gruppo creato da un superadmin non si
    ripubblica e non si annuncia."""
    published, current = _canvas(filters=2), _canvas(filters=1)
    record = _record(published)
    with patch.object(eseguibili_db, "get_eseguibile", return_value=record), \
         patch.object(eseguibili_db, "get_eseguibile_version", return_value=_version(published)), \
         patch.object(eseguibili_db, "list_eseguibili_by_source",
                      return_value=[eseguibili_db.EseguibileSourceRefRow(id="exec_1", name="x")]), \
         patch.object(eseguibili_service.project_access_context, "load_project_publish_context",
                      return_value=_project(current)), \
         patch("app.core.eseguibili.router.compute_can_edit", lambda *a: False):
        assert _client().get("/eseguibili/by-source/p1").json() == []


# ── Controllo veloce dopo P1–P7 (09/11/2026): D1 confronto simmetrico, D2
# una riga che non si confronta non rompe la lista ─────────────────────────

def test_d1_one_canvas_refused_by_the_model_compares_both_raw(monkeypatch):
    """Se il modello rifiuta anche uno solo dei due canvas, si confrontano
    entrambi grezzi: mai uno normalizzato contro uno grezzo (falso «cambiato»)."""
    from app.core.eseguibili import service

    canvas = {"nodes": [{"id": "n1", "type": "result", "data": {}}], "edges": []}
    real = service.CanvasState.model_validate
    calls = {"n": 0}

    def refuse_first(value, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ValueError("canvas vecchio fuori schema")
        return real(value, *args, **kwargs)

    monkeypatch.setattr(service.CanvasState, "model_validate", refuse_first)
    assert service.source_changed(canvas, dict(canvas)) is False


def test_d2_a_row_that_cannot_be_compared_does_not_break_the_list(monkeypatch):
    from app.core.eseguibili import service

    # Archi non iterabili: nessuna eccezione, nessun falso avviso.
    weird = {"nodes": [], "edges": 5}
    assert service.source_changed(weird, weird) is False

    class _Row:
        id = "exec_x"
        version_canvas = {"nodes": []}
        project_canvas = {"nodes": []}

    monkeypatch.setattr(service.eseguibili_db, "list_source_canvases", lambda ids: [_Row()])

    def boom(*_args, **_kwargs):
        raise TypeError("canvas corrotto")

    monkeypatch.setattr(service, "source_changed", boom)
    assert service.source_changed_by_id(["exec_x"]) == {"exec_x": None}
