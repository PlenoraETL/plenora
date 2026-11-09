"""«Utente che esegue»: trasformazione del nome utente e filtro nel database.

Regola (``app/shared/execution/runtime_identity``, sezione TRASFORMAZIONE):
il valore dell'input e' il risultato della «Trasformazione» (motore della
Formula, variabile ``utente``) sul nome utente dell'identita', calcolato SOLO
dal server in ``resolve_identity_inputs``. Ogni test qui fallisce senza la
funzione:

1. risoluzione: utente autenticato, nome di prova, prova dall'editor;
   errore di valutazione -> blocco ``runtime_identity_transform_failed``;
   «trasformato ma senza righe» non e' un errore;
2. manifest e pubblicazione: la trasformazione entra nel manifest;
   un'espressione non valida e' rifiutata (pubblicazione, Valida);
3. Estrai: il valore va nel WHERE come UN parametro SQL, anche con
   caratteri pericolosi, funzioni SQL, virgole;
4. manomissione: il client non imposta ne' il valore ne' la trasformazione;
5. editor: anteprima, Valida, sorgente di Manipola e anteprima del pannello
   («E0041 -> 004_IS»).
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import runtime as executable_runtime
from app.core.eseguibili import service as eseguibili_service
from app.core.eseguibili.identity_transform import identity_values, preview_transform, transform_problem
from app.core.eseguibili.models import ExecutableManifest
from app.core.projects.canvas_models import CanvasState
from app.shared import error_codes
from app.shared.execution.runtime_identity import RuntimeIdentity, RuntimeIdentityError
from tests2.eseguibili.unit.conftest import build_client
from tests2.eseguibili.unit.test_runtime_identity_inputs import (
    _bound_value,
    _code,
    _estrai_canvas,
    _manifest,
    _manipola_canvas,
    _Patched,
    _project_app,
)
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

RULE = "utente.str.slice(1, 4) + '_IS'"
FAILED = error_codes.RUNTIME_IDENTITY_TRANSFORM_FAILED
DEFS = [SimpleNamespace(key="utente", label="Utente", kind="current_user", transform=RULE)]


def _user(username: str):
    return RuntimeIdentity.authenticated(build_current_user(username=username))


# ---------------------------------------------------------------- 1. risoluzione


@pytest.mark.parametrize("identity,value", [
    (_user("E0041"), "004_IS"),
    (_user("E0071"), "007_IS"),
    (RuntimeIdentity.preview_of(_estrai_canvas(preview_username="E0041")), "004_IS"),
    (RuntimeIdentity.test_run(_estrai_canvas(preview_username="E0041")), "004_IS"),
    # Nomi utente di sole cifre restano testo (nessuna conversione in numero).
    (_user("12345"), "234_IS"),
])
def test_the_value_is_the_transformed_username_for_every_identity(identity, value):
    assert identity_values(DEFS, identity=identity, client_values={}) == {"utente": value}


def test_without_transform_the_value_is_the_username():
    plain = [SimpleNamespace(key="utente", label="Utente", kind="current_user", transform=None)]
    assert identity_values(plain, identity=_user("E0041"), client_values={}) == {"utente": "E0041"}


def test_transformed_without_rows_is_not_an_error():
    """``admin`` -> ``dmi_IS``: un valore valido che non trova righe (file
    vuoto), non un errore; un nome corto -> ``_IS``, idem."""
    assert identity_values(DEFS, identity=_user("admin"), client_values={}) == {"utente": "dmi_IS"}
    assert identity_values(DEFS, identity=_user("E"), client_values={}) == {"utente": "_IS"}


@pytest.mark.parametrize("expression,username,words", [
    ("utente + 1", "E0041", "non si applica"),
    ("utente.str.extract('([0-9]+)', expand=False)", "admin", "nessun valore"),
    ("utente.str.len()", "E0041", "non e' un testo"),
    ("utente == 'E0041'", "E0041", "non e' un testo"),
    ("utente.str.slice(9)", "E0041", "testo vuoto"),
])
def test_a_failing_transform_stops_the_run_with_a_clear_message(expression, username, words):
    defs = [SimpleNamespace(key="utente", label="Istituto", kind="current_user", transform=expression)]
    with pytest.raises(RuntimeIdentityError) as err:
        identity_values(defs, identity=_user(username), client_values={})
    assert err.value.code == FAILED
    message = err.value.message
    assert f"«{username}»" in message and "«Istituto»" in message and words in message
    assert "si ferma" in message


# ---------------------------------------------------------------- 2. manifest e pubblicazione


def test_the_manifest_carries_the_transform_and_never_the_test_username():
    manifest = _manifest("estrai", _estrai_canvas(identity_transform=f"  {RULE} ", preview_username="E0041"))
    assert manifest.inputs[0].transform == RULE
    assert "E0041" not in str(manifest.model_dump())


def test_a_transform_on_a_client_input_is_ignored():
    canvas = _estrai_canvas(identity_transform=RULE)
    canvas["nodes"][-1]["data"]["input_kind"] = "text"
    assert _manifest("estrai", canvas).inputs[0].transform is None


INVALID = [
    ("utente.__class__", "non e' una formula ammessa"),
    ("utente +", "non e' una formula ammessa"),
    ("__import__('os')", "non e' una formula ammessa"),
    ("istituto.str.upper()", "nomi sconosciuti (istituto)"),
    ("'004_IS'", "non usa «utente»"),
    ("utente + '" + "x" * 600 + "'", "supera 500 caratteri"),
]


@pytest.mark.parametrize("expression,words", INVALID)
def test_an_invalid_transform_is_refused_with_its_reason(expression, words):
    assert words in (transform_problem(expression) or "")
    with pytest.raises(ValueError) as err:
        _manifest("estrai", _estrai_canvas(identity_transform=expression))
    assert "Utente che esegue" in str(err.value) and words in str(err.value)


def test_publication_refuses_an_invalid_transform(monkeypatch):
    canvas = _estrai_canvas(identity_transform="istituto.str.upper()")
    monkeypatch.setattr(eseguibili_service.project_access_context, "load_project_publish_context",
                        lambda _pid: SimpleNamespace(project_id="p", group_id="grp-1", project_type="estrai",
                                                     canvas=canvas, default_connection_id="conn"))
    created = []
    monkeypatch.setattr(eseguibili_db, "create_eseguibile", lambda **kw: created.append(kw))
    with pytest.raises(ValueError, match="nomi sconosciuti"):
        eseguibili_service.create_from_project("p", "Pratiche", "grp-1", "u-1")
    assert created == []


def test_publication_stores_the_transform(monkeypatch):
    canvas = _estrai_canvas(identity_transform=RULE)
    monkeypatch.setattr(eseguibili_service.project_access_context, "load_project_publish_context",
                        lambda _pid: SimpleNamespace(project_id="p", group_id="grp-1", project_type="estrai",
                                                     canvas=canvas, default_connection_id="conn"))
    monkeypatch.setattr(eseguibili_service, "hydrate_manifest_connection_options", lambda m, **_: m)
    created = []
    monkeypatch.setattr(eseguibili_db, "create_eseguibile", lambda **kw: created.append(kw) or SimpleNamespace(id="e"))
    eseguibili_service.create_from_project("p", "Pratiche", "grp-1", "u-1")
    assert created[0]["manifest"]["inputs"][0]["transform"] == RULE


def test_validate_reports_an_invalid_transform(monkeypatch):
    from app.core.projects import router as projects
    canvas = _manipola_canvas(identity_transform="utente.__class__", preview_username="E0041")
    client, _ = _project_app("manipola", canvas, monkeypatch)
    monkeypatch.setattr(projects, "validate_project_chain",
                        lambda *a, **k: SimpleNamespace(to_dict=lambda: {"issues": []}, has_errors=False))
    issues = client.post("/api/projects/p/validate", json={"canvas": canvas, "inputs": {}}).json()["issues"]
    assert issues and issues[0]["code"] == "runtime_inputs.invalid"
    assert "non e' una formula ammessa" in issues[0]["message"]


# ---------------------------------------------------------------- 3. Estrai: WHERE con bind


def _estrai_sql(canvas: dict, identity: RuntimeIdentity) -> dict:
    from app.core.estrai import query_plan
    from app.core.estrai.query_generator import QueryGenerator

    resolved, _, summary = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("estrai", canvas), values={},
        group_id="grp-1", identity=identity)
    state = CanvasState(**resolved)
    info = query_plan.sql_query(QueryGenerator(), state.nodes, state.edges,
                                query_plan.estrai_result_node(state.nodes),
                                {"db_type": "postgres", "db_schema": "public"})
    info["summary"] = summary
    return info


def _with_operator(canvas: dict, operator: str) -> dict:
    canvas["nodes"][0]["filters"][0]["operator"] = operator
    return canvas


@pytest.mark.parametrize("username,transform,operator,value,clause", [
    ("E0071", RULE, "=", "007_IS", "= :p_0"),
    # Caratteri pericolosi: restano dentro il parametro.
    ("x' OR '1'='1; DROP TABLE t; --", "utente.str.upper() + '_IS'", "=",
     "X' OR '1'='1; DROP TABLE T; --_IS", "= :p_0"),
    # Somiglia a una funzione SQL: senza il segno «letterale» entrerebbe come NOW().
    ("NOW()", None, "=", "NOW()", "= :p_0"),
    ("e0041", "'GETDATE()' + utente", "=", "GETDATE()e0041", "= :p_0"),
    # Virgola con IN: un solo valore, non una lista.
    ("rossi,bianchi", None, "IN", "rossi,bianchi", "IN (:p_0)"),
])
def test_estrai_binds_the_value_as_one_sql_parameter(username, transform, operator, value, clause):
    extra = {"identity_transform": transform} if transform else {}
    info = _estrai_sql(_with_operator(_estrai_canvas(**extra), operator), _user(username))
    sql = info["sql"]
    assert f"owner {clause}" in sql, sql
    assert list(info["params"].values()) == [value]
    assert value not in sql and username not in sql
    expected = {"kind": "current_user", "provided": True, "username": username}
    if transform:
        expected["value"] = value
    assert info["summary"]["utente"] == expected


@pytest.mark.parametrize("operator", ["LIKE", "NOT LIKE", "!=", ">", "<", ">=", "<=", "NOT IN", "BETWEEN"])
def test_estrai_refuses_operators_that_would_widen_the_data(operator):
    """D1/O1 (controllo 09/11/2026): un valore del server restringe i dati di
    chi esegue; solo «uguale» e «è uno di» selezionano esattamente quel valore.
    LIKE con «004_IS» troverebbe anche altri istituti («_» e «%» sono jolly)."""
    with pytest.raises(ValueError, match="si confronta solo con «uguale» o «è uno di»"):
        _estrai_sql(_with_operator(_estrai_canvas(identity_transform=RULE), operator), _user("E0071"))


def test_estrai_refuses_the_runtime_user_inside_an_or_group():
    canvas = _estrai_canvas(identity_transform=RULE)
    filters = canvas["nodes"][0]["filters"]
    filters[0]["group"] = 0
    filters.append({"column": "owner", "operator": "=", "value": "altro", "group": 1})
    with pytest.raises(ValueError, match="non può stare in condizioni con «oppure»"):
        _estrai_sql(canvas, _user("E0071"))


@pytest.mark.parametrize("expression", [
    "utente + 'a' * 9**9**9",
    "utente.str.pad(10**10)",
    "utente.str.pad(100000)",
    "utente * 1000",
    "utente + '" + "x" * 300 + "'",
])
def test_costly_transforms_are_refused_before_running(expression):
    """D2: potenze, ripetizioni e numeri o testi grandi bloccherebbero il
    processo anche su una riga; la trasformazione ammette solo «+»."""
    assert transform_problem(expression) is not None
    with pytest.raises(ValueError):
        preview_transform(expression, "E0041")


def test_rule_still_accepted_after_the_cost_limits():
    assert transform_problem(RULE) is None
    assert preview_transform(RULE, "E0071") == "007_IS"


def test_panel_preview_is_for_who_can_edit_projects():
    """D2: l'anteprima e' per chi scrive canvas (non il viewer)."""
    import inspect
    from app.core.projects import router as projects
    src = inspect.getsource(projects.preview_identity_transform)
    assert "Depends(require_user_or_above)" in src


def test_client_text_inputs_keep_their_reading():
    """Il segno «letterale» vale solo per i valori del server: un input testo
    del client collegato allo stesso filtro e' letto come prima."""
    from app.core.estrai import query_plan
    from app.core.estrai.query_generator import QueryGenerator

    canvas = _estrai_canvas()
    canvas["nodes"][-1]["data"]["input_kind"] = "text"
    resolved, _, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("estrai", canvas), values={"utente": "NOW()"},
        group_id="grp-1", identity=RuntimeIdentity.unattended())
    assert "value_literal" not in str(resolved)
    state = CanvasState(**resolved)
    info = query_plan.sql_query(QueryGenerator(), state.nodes, state.edges,
                                query_plan.estrai_result_node(state.nodes),
                                {"db_type": "postgres", "db_schema": "public"})
    assert "owner = NOW()" in info["sql"]


# ---------------------------------------------------------------- 4. manomissione


def _rows(canvas: dict):
    manifest = _manifest("estrai", canvas).model_dump()
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1", eseguibile_id="exe-1", version_number=1, schema_version=2, project_type="estrai",
        source_project_id="p", source_hash="h", canvas=canvas, manifest=manifest, status="published",
        created_by="u-1", created_at="2026-11-09T00:00:00+00:00")
    executable = eseguibili_db.EseguibileRow(
        id="exe-1", name="Immobili", description="", canvas=canvas, expected_columns=[],
        default_output_format="csv", group_id="grp-1", created_by="u-1",
        created_at="2026-11-09T00:00:00+00:00", updated_at="2026-11-09T00:00:00+00:00",
        project_type="estrai", active_version_id="version-1", manifest=manifest)
    return executable, version


def _run(canvas: dict, user, body: dict):
    executable, version = _rows(canvas)
    task = SimpleNamespace(task_id="task-1")
    patches = [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._save_task_owner"),
        patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"),
        patch("app.core.eseguibili.service.eseguibili_db.create_eseguibile_run", return_value="run-1"),
        patch("app.core.eseguibili.service.eseguibili_db.bind_eseguibile_run_task"),
    ]
    with _Patched(patches), patch("app.core.orchestrator.task_service.task_service.create_pipeline_task",
                                   return_value=task) as submit:
        response = build_client(user).post("/api/eseguibili/exe-1/runs", json=body)
    if response.status_code != 200:
        return response.status_code, _code(response)
    return 200, _bound_value(submit.call_args.kwargs["execution_contract"]["canvas"], "estrai")


E0041 = build_current_user(username="E0041")


def test_run_uses_the_published_transform_of_the_authenticated_user():
    assert _run(_estrai_canvas(identity_transform=RULE), E0041, {"inputs": {}}) == (200, "004_IS")


@pytest.mark.parametrize("body", [
    {"inputs": {"utente": "999_IS"}},
    {"inputs": {"utente": "E0099"}},
])
def test_the_client_cannot_send_the_value(body):
    assert _run(_estrai_canvas(identity_transform=RULE), E0041, body) == (400, error_codes.RUNTIME_IDENTITY_CLIENT_VALUE)


@pytest.mark.parametrize("body", [
    {"inputs": {}, "transform": "'999_IS' + utente"},
    {"inputs": {}, "identity_transform": "'999_IS' + utente"},
])
def test_the_client_cannot_send_the_transform(body):
    status, _ = _run(_estrai_canvas(identity_transform=RULE), E0041, body)
    assert status == 422


def test_the_run_reads_the_transform_from_the_manifest_not_the_canvas():
    """La copia del canvas nella versione non conta: vale il manifest."""
    canvas = _estrai_canvas(identity_transform=RULE)
    executable, version = _rows(canvas)
    tampered = copy.deepcopy(canvas)
    tampered["nodes"][-1]["data"]["identity_transform"] = "'999_IS' + utente"
    resolved, _, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=tampered,
        manifest=ExecutableManifest.model_validate(version.manifest), values={},
        group_id="grp-1", identity=_user("E0041"))
    assert _bound_value(resolved, "estrai") == "004_IS"


# ---------------------------------------------------------------- 5. editor


def test_preview_uses_the_transformed_test_username(monkeypatch):
    canvas = _manipola_canvas(identity_transform=RULE, preview_username="E0041")
    client, submitted = _project_app("manipola", canvas, monkeypatch)
    assert client.get("/api/projects/p/preview").status_code == 200
    assert _bound_value(submitted.call_args.kwargs["execution_contract"]["canvas"], "manipola") == "004_IS"


def test_validate_with_inputs_uses_the_transformed_test_username(monkeypatch):
    from app.core.projects import router as projects
    canvas = _manipola_canvas(identity_transform=RULE, preview_username="E0041")
    client, _ = _project_app("manipola", canvas, monkeypatch)
    seen = {}

    def chain(project_id, load, **_):
        seen["canvas"] = load(project_id)[0]
        return SimpleNamespace(to_dict=lambda: {"issues": []}, has_errors=False)

    monkeypatch.setattr(projects, "validate_project_chain", chain)
    response = client.post("/api/projects/p/validate", json={"canvas": canvas, "inputs": {}})
    assert response.json()["issues"] == []
    assert _bound_value(seen["canvas"].model_dump(), "manipola") == "004_IS"


def test_manipola_source_holds_the_transformed_value():
    canvas = {"nodes": [
        {"id": "utente", "type": "project_input", "position": {"x": 0, "y": 0}, "data": {
            "input_key": "utente", "label": "Utente", "input_kind": "current_user", "as_source": True,
            "source_column": "istituto", "identity_transform": RULE}},
        {"id": "out", "type": "result", "position": {"x": 0, "y": 0}},
    ], "edges": [{"id": "e", "source": "utente", "target": "out"}]}
    resolved, _, _ = executable_runtime.resolve_runtime_inputs(
        eseguibile_id="exe-1", canvas=canvas, manifest=_manifest("manipola", canvas), values={},
        group_id="grp-1", identity=_user("E0041"))
    node = next(n for n in resolved["nodes"] if n["id"] == "utente")
    assert node["type"] == "staticValues"
    assert node["fields"] == [{"name": "istituto", "value": "004_IS", "field_type": "string"}]


@pytest.mark.parametrize("expression,username,expected", [
    (RULE, "E0041", {"success": True, "value": "004_IS", "message": None}),
    (RULE, " E0041 ", {"success": True, "value": "004_IS", "message": None}),
    ("", "E0041", {"success": True, "value": "E0041", "message": None}),
])
def test_panel_preview_shows_the_transformed_test_username(expression, username, expected, monkeypatch):
    client, _ = _project_app("manipola", _manipola_canvas(), monkeypatch)
    response = client.post("/api/projects/identity-transform-preview",
                           json={"expression": expression, "username": username})
    assert response.status_code == 200 and response.json() == expected


@pytest.mark.parametrize("expression,words", [
    ("utente.__class__", "non e' una formula ammessa"),
    ("utente.str.slice(9)", "testo vuoto"),
])
def test_panel_preview_explains_why_it_fails(expression, words, monkeypatch):
    client, _ = _project_app("manipola", _manipola_canvas(), monkeypatch)
    body = client.post("/api/projects/identity-transform-preview",
                       json={"expression": expression, "username": "E0041"}).json()
    assert body["success"] is False and words in body["message"]


def test_panel_preview_is_the_same_rule_as_the_run():
    assert preview_transform(RULE, "E0041") == identity_values(
        DEFS, identity=_user("E0041"), client_values={})["utente"]


# ---------------------------------------------------------------- 6. ripubblicazione (V1)


@pytest.mark.parametrize("old_transform,new_transform", [
    (None, RULE),            # manifest pubblicato prima della funzione -> con trasformazione
    (RULE, None),            # trasformazione tolta
    (RULE, "utente.str.upper()"),
])
def test_republish_does_not_report_a_kind_change_for_the_transform(old_transform, new_transform):
    """``input_changes`` confronta tipo e collegamenti: un input «Utente che
    esegue» con lo stesso tipo e gli stessi collegamenti non e' «kind_changed»
    solo perche' la trasformazione cambia o compare."""
    old_canvas = _estrai_canvas(**({"identity_transform": old_transform} if old_transform else {}))
    published = _manifest("estrai", old_canvas).model_dump(mode="json")
    if old_transform is None:
        published["inputs"][0].pop("transform")  # forma salvata prima della funzione
    new_canvas = _estrai_canvas(**({"identity_transform": new_transform} if new_transform else {}))
    new = _manifest("estrai", new_canvas)
    assert eseguibili_service.input_changes(published, new.inputs, new.bindings, new_canvas) == []
