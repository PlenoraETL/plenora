"""Filtro «Utente che esegue» ignorato in silenzio: correzione (11/11/2026).

Raccolta: ``docs/audits/identita-binding-ignorato-raccolta-2026-11-11.md``.

Caso reale (produzione): Estrai con radice ``prt_cd_valuation`` e
``prt_cd`` collegata «in avanti» (arco radice -> collegata, INNER JOIN su
``cd_id`` con ``LTRIM_RTRIM``), tutte le colonne di ``prt_cd`` nascoste,
filtri su ``prt_cd``: ``date_end IS NULL`` e ``ac_id = <Utente che
esegue>``. La query era ``FROM afm.prt_cd_valuation`` senza JOIN ne' filtri:
tutte le righe di tutti gli istituti, senza errore.

Due regole, per costruzione:

1. JOIN nei due versi (``canvas_preview_scope.is_join_edge``): la tabella
   collegata entra in OGNI percorso che costruisce SQL o sceglie i nodi
   (test (i) e la loro enumerazione).
2. Ogni binding di un input con valore iniettato dal server deve avere
   effetto dopo la costruzione (``projects.identity_effect``); altrimenti
   ``runtime_identity_unbound`` in Valida, anteprima della query, avvio,
   stima, prova, anteprima, ricalcolo isolato a monte, pubblicazione
   (test (ii) per ogni modo della raccolta, (iii) enumerazione dei
   percorsi).
"""

from __future__ import annotations

import ast
import copy
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.eseguibili import upstream_plan
from app.core.projects.identity_effect import (
    assert_identity_bindings_effective,
    identity_binding_problems,
)
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableInputBinding, ExecutableInputDefinition
from app.core.eseguibili.runtime import resolve_runtime_inputs
from app.core.estrai import query_plan
from app.core.estrai.executor import EstraiExecutor
from app.core.estrai.query_generator import QueryGenerator
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import (
    apply_identity_inputs,
    apply_preview_inputs,
    apply_project_runtime_inputs,
    apply_test_run_inputs,
)
from app.core.projects import canvas_validation, schema_execution_sources
from app.core.projects.canvas_models import CanvasState
from app.core.projects.canvas_preview_scope import (
    flow_node_ids,
    preview_canvas,
    run_canvas,
    unreached_node_ids,
)
from app.core.projects.query_preview import describe_estrai_query
from app.shared import error_codes
from app.shared.execution.runtime_identity import RuntimeIdentity, RuntimeIdentityError
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

UNBOUND = error_codes.RUNTIME_IDENTITY_UNBOUND
POS = {"x": 0, "y": 0}
MSSQL = {"db_type": "mssql", "db_schema": "afm"}
BACKEND = Path(__file__).resolve().parents[3]
MARIO = RuntimeIdentity.authenticated(build_current_user(username="mario"), owner_group_id="g")


# ------------------------------------------------------------------ canvas


def _table(node_id, table, columns, *, visible=True, filters=None, **extra):
    node = {"id": node_id, "type": "source", "position": POS, "table_name": table,
            "columns": [{"name": c, "type": "str", "visible": visible} for c in columns], **extra}
    if filters is not None:
        node["filters"] = filters
    return node


def _result(**extra):
    return {"id": "out", "type": "result", "position": POS, "fileName": "o", "format": "csv", **extra}


def _identity(target, field, *, key="utente", label="Utente che esegue", **data):
    return {"id": f"in_{key}", "type": "project_input", "position": POS, "data": {
        "input_key": key, "label": label, "input_kind": "current_user", "preview_username": "mario",
        "bindings": [{"target_node_id": target, "target_field": field}], **data}}


def _join(source, target, *, join_type="inner", edge_id="j"):
    return {"id": edge_id, "source": source, "target": target, "join_type": join_type,
            "source_column": "cd_id", "target_column": "cd_id",
            "join_conditions": [{"source_column": "cd_id", "target_column": "cd_id",
                                 "source_func": "LTRIM_RTRIM", "target_func": "LTRIM_RTRIM"}]}


def _out(source="val"):
    return {"id": f"o_{source}", "source": source, "target": "out", "join_type": "output"}


def _binding_edge(source, target):
    return {"id": f"b_{source}_{target}", "source": source, "target": target,
            "data": {"edge_kind": "parameter_binding"}}


CD_FILTERS = [
    {"column": "date_end", "operator": "IS NULL", "value": ""},
    {"column": "ac_id", "operator": "=", "value": ""},
]


def real_case(*, direction="forward", join_type="inner", cd_visible=False):
    """Il caso di produzione: radice prt_cd_valuation, prt_cd nascosta con
    i filtri, «Utente che esegue» su ``filters.1.value`` di prt_cd."""
    val = _table("val", "prt_cd_valuation", ["cd_id", "valore", "data_valutazione"])
    cd = _table("cd", "prt_cd", ["cd_id", "ac_id", "date_end"], visible=cd_visible, filters=copy.deepcopy(CD_FILTERS))
    edge = _join("val", "cd", join_type=join_type) if direction == "forward" else _join("cd", "val", join_type=join_type)
    return {"nodes": [val, cd, _result(), _identity("cd", "filters.1.value")],
            "edges": [edge, _out("val"), _binding_edge("in_utente", "cd")]}


def _manifest(canvas, project_type="estrai"):
    return compile_manifest(for_project=True, source_project_id="p", project_type=project_type, canvas=canvas)


def _resolve(canvas, project_type="estrai", **kwargs):
    return resolve_runtime_inputs(eseguibile_id="p", canvas=canvas, manifest=_manifest(canvas, project_type),
                                  values={}, group_id="g", identity=MARIO, **kwargs)


def _executed_sql(resolved_canvas):
    """Il SQL dell'esecuzione: ambito dell'orchestratore (run_canvas) e il
    piano dell'esecutore (query_plan.sql_query) con le opzioni di MSSQL."""
    scope = run_canvas(CanvasState(**resolved_canvas))
    result = query_plan.estrai_result_node(scope.nodes)
    return query_plan.sql_query(QueryGenerator(), scope.nodes, scope.edges, result, MSSQL)


def _problems(canvas, project_type="estrai", **kwargs):
    return identity_binding_problems(canvas, _manifest(canvas, project_type), **kwargs)


def _contract(canvas, project_type="estrai"):
    return PipelineExecutionContract(project_id="p", project_type=project_type, group_id="g", canvas=canvas,
                                     runtime_context={"default_connection_id": "conn"})


# ------------------------------------------- (i) la tabella entra in ogni SQL


@pytest.mark.parametrize("direction", ["forward", "backward"])
@pytest.mark.parametrize("cd_visible", [False, True])
def test_real_case_execution_sql_has_the_join_and_both_filters(direction, cd_visible):
    resolved, _cleanup, _summary = _resolve(real_case(direction=direction, cd_visible=cd_visible))
    info = _executed_sql(resolved)
    sql = info["sql"]
    assert set(info["tables_used"]) == {"prt_cd_valuation", "prt_cd"}
    assert "INNER JOIN afm.prt_cd" in sql or "INNER JOIN afm.prt_cd_valuation" in sql
    assert "LTRIM(RTRIM(prt_cd_valuation.cd_id)) = LTRIM(RTRIM(prt_cd.cd_id))" in sql \
        or "LTRIM(RTRIM(prt_cd.cd_id)) = LTRIM(RTRIM(prt_cd_valuation.cd_id))" in sql
    assert "prt_cd.date_end IS NULL" in sql
    param = re.search(r"prt_cd\.ac_id = :(p_\d+)", sql).group(1)
    assert info["params"][param] == "mario"
    # Colonne nascoste: restano fuori dalla SELECT, la tabella resta nella query.
    select = sql.split("FROM")[0]
    assert ("prt_cd.ac_id" in select) is cd_visible


def test_real_case_the_query_starts_from_the_root():
    """La radice resta il FROM (nessun arco la raggiunge), la collegata in
    avanti entra con la JOIN: prima la query era il solo ``FROM
    afm.prt_cd_valuation`` (``tables_used: ["prt_cd_valuation"]``)."""
    canvas = CanvasState(**real_case())
    tables = [node.id for node in QueryGenerator().query_tables(canvas.nodes, canvas.edges, "out")]
    assert tables == ["val", "cd"]


@pytest.mark.parametrize("join_type", ["inner", "left"])
def test_left_join_filters_stay_in_where_as_for_visible_tables(join_type):
    """Il repo mette i filtri di ogni tabella nella WHERE, anche in LEFT JOIN
    (una tabella con colonne visibili si comporta cosi' da sempre): la
    tabella senza colonne visibili ha lo stesso trattamento."""
    hidden = _executed_sql(_resolve(real_case(join_type=join_type, cd_visible=False))[0])["sql"]
    shown = _executed_sql(_resolve(real_case(join_type=join_type, cd_visible=True))[0])["sql"]
    assert f"{join_type.upper()} JOIN" in hidden
    assert hidden.split("FROM")[1] == shown.split("FROM")[1]
    assert "WHERE prt_cd.date_end IS NULL AND prt_cd.ac_id = :p_0" in " ".join(hidden.split())


def test_scope_rule_reaches_the_forward_joined_table():
    canvas = real_case()
    assert unreached_node_ids(CanvasState(**canvas)) == set()
    assert {"val", "cd", "out"} <= {n.id for n in run_canvas(CanvasState(**canvas)).nodes}
    assert {"val", "cd", "out"} <= flow_node_ids(canvas)
    # L'anteprima dell'Output vede le JOIN nei due versi; un passo resta
    # «il passo e i suoi antenati».
    assert {n.id for n in preview_canvas(CanvasState(**canvas), "out").nodes} >= {"val", "cd"}
    assert {n.id for n in preview_canvas(CanvasState(**canvas), "val").nodes} == {"val"}


def test_bridge_table_without_columns_or_filters_joins_two_tables():
    """Ponte: val -> ponte -> cd, il ponte senza colonne visibili ne' filtri."""
    canvas = real_case()
    canvas["nodes"].insert(1, _table("ponte", "prt_ponte", ["cd_id"], visible=False))
    canvas["edges"][0] = _join("val", "ponte", edge_id="j1")
    canvas["edges"].append(_join("ponte", "cd", edge_id="j2"))
    info = _executed_sql(_resolve(canvas)[0])
    assert set(info["tables_used"]) == {"prt_cd_valuation", "prt_ponte", "prt_cd"}
    assert info["params"] == {"p_0": "mario"}


def _preview_sql(canvas, node_id=None):
    db = MagicMock()
    db.execute_raw.return_value = ([], ["cd_id"])
    executor = EstraiExecutor(MagicMock(), db, QueryGenerator(), MagicMock())
    executor._resolve_connection = lambda _cid: (SimpleNamespace(type="mssql", db_schema="afm"), "db")
    executor._assert_connection_visible_for_project = lambda *a, **k: None
    context = {"default_connection_id": "conn", "group_id": "g"}
    if node_id is not None:
        context["preview_node_id"] = node_id
    result = executor.preview("p", CanvasState(**canvas), 10, execution_context=context)
    assert result["success"], result
    return db.execute_raw.call_args.args[1], db.execute_raw.call_args.args[2]


def _execute_sql(canvas):
    query_executor = MagicMock()
    query_executor.execute_query.return_value = {"success": True, "rows_count": 0}
    executor = EstraiExecutor(MagicMock(), query_executor, QueryGenerator(), MagicMock())
    executor._resolve_connection = lambda _cid: (SimpleNamespace(type="mssql", db_schema="afm", id="conn"), "db")
    executor._assert_connection_visible_for_project = lambda *a, **k: None
    result = executor.execute("p", run_canvas(CanvasState(**canvas)), execution_context={
        "default_connection_id": "conn", "group_id": "g", "persist_contract": False})
    assert result["success"], result
    call = query_executor.execute_query.call_args.kwargs
    return call["sql"], call["params"]


@pytest.mark.parametrize("path", ["execute", "preview_output", "preview_result_step", "query_preview",
                                  "validate", "schema"])
def test_every_sql_path_keeps_the_joined_table(path, monkeypatch):
    """(i) Enumerazione dei percorsi che costruiscono SQL o scelgono i nodi
    (vedi :data:`SQL_BUILDERS`): la tabella collegata in avanti, senza
    colonne visibili, entra con la JOIN e i filtri."""
    canvas = real_case()
    resolved = _resolve(canvas)[0]
    if path == "execute":
        sql, params = _execute_sql(resolved)
    elif path == "preview_output":
        sql, params = _preview_sql(resolved)
    elif path == "preview_result_step":
        sql, params = _preview_sql(resolved, node_id="out")
    elif path == "query_preview":
        described = describe_estrai_query(CanvasState(**canvas), project_id="p", project_type="estrai",
                                          default_connection_id="conn", load_sql_options=lambda _cid: MSSQL)
        assert described["error"] is None, described
        assert described["tables_used"] == ["prt_cd_valuation", "prt_cd"]
        sql, params = described["sql"], {p["name"]: p for p in described["params"]}
        assert params["p_0"]["input_key"] == "utente"
    elif path == "validate":
        monkeypatch.setattr(schema_execution_sources, "load_sql_options", lambda _cid: MSSQL)
        report = canvas_validation.validate_canvas(CanvasState(**canvas), project_id="p", project_type="estrai",
                                                   execution_context={"default_connection_id": "conn"})
        codes = {(i.node_id, i.code, i.severity) for i in report.issues}
        assert not any(code in {"structure.unreached", "SOURCE_NO_VISIBLE_COLUMNS", "sql.invalid", UNBOUND}
                       for _node, code, _sev in codes), codes
        return
    else:  # schema delle colonne dell'Output (/schemas)
        state = CanvasState(**canvas)
        columns = schema_execution_sources.sql_projection(state.nodes, state.edges,
                                                          query_plan.estrai_result_node(state.nodes))
        assert [c["name"] for c in columns] == ["cd_id", "valore", "data_valutazione"]
        return
    assert "JOIN afm.prt_cd " in sql + " " and "prt_cd.date_end IS NULL" in sql and "prt_cd.ac_id = :p_0" in sql
    assert params


#: Ogni funzione di backend/app e backend/workers che costruisce il SQL di
#: Estrai o la lista delle sue tabelle, con il test che la copre.
SQL_BUILDERS = {
    "app/core/estrai/executor.py::_execute_database": "execute",
    "app/core/estrai/executor.py::_preview_database": "preview_output, preview_result_step",
    "app/core/estrai/query_plan.py::sql_query": "tutti i percorsi di esecuzione",
    "app/core/estrai/query_generator.py::generate_sql": "tutti",
    "app/core/projects/query_preview.py::describe_estrai_query": "query_preview",
    "app/core/projects/canvas_validation.py::_canvas_issues": "validate",
    "app/core/projects/schema_execution_sources.py::sql_projection": "schema",
    "app/core/projects/identity_effect.py::_check_estrai_query": "test (ii)",
}
_SQL_CALLS = {"generate_sql", "sql_query", "query_tables", "_trova_tabelle_connesse"}


def _calls_in_functions(names: set[str]) -> set[str]:
    found = set()
    for root in (BACKEND / "app", BACKEND / "workers"):
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if isinstance(node, ast.Call):
                        called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                        if called in names:
                            found.add(f"{path.relative_to(BACKEND).as_posix()}::{func.name}")
    return found


def test_every_sql_builder_is_enumerated():
    """Un nuovo punto che costruisce SQL di Estrai deve entrare in
    :data:`SQL_BUILDERS` con il suo test: altrimenti questo test fallisce."""
    found = _calls_in_functions(_SQL_CALLS)
    # query_tables chiama la visita; generate_sql chiama query_tables.
    found.discard("app/core/estrai/query_generator.py::query_tables")
    assert found == set(SQL_BUILDERS), found ^ set(SQL_BUILDERS)


def test_joined_table_without_visible_columns_is_not_a_validation_error(monkeypatch):
    """SOURCE_NO_VISIBLE_COLUMNS resta per una tabella da sola, non per una
    tabella in JOIN (filtra o fa da ponte)."""
    monkeypatch.setattr(schema_execution_sources, "load_sql_options", lambda _cid: MSSQL)
    alone = {"nodes": [_table("cd", "prt_cd", ["cd_id"], visible=False), _result()], "edges": [_out("cd")]}
    report = canvas_validation.validate_canvas(CanvasState(**alone), project_id="p", project_type="estrai",
                                               execution_context={"default_connection_id": "conn"})
    assert "SOURCE_NO_VISIBLE_COLUMNS" in {i.code for i in report.issues}


# ----------------------------------- (ii) ogni modo senza effetto si blocca


def _disconnected_table():
    """La tabella con il filtro non e' collegata a niente."""
    canvas = real_case()
    canvas["edges"] = [e for e in canvas["edges"] if e["id"] != "j"]
    return canvas


def _empty_column_filter():
    """Il filtro collegato non ha una colonna: la WHERE lo salta."""
    canvas = real_case()
    canvas["nodes"][1]["filters"][1]["column"] = ""
    return canvas


def _or_group():
    """Il filtro collegato sta in un gruppo «oppure»."""
    canvas = real_case()
    canvas["nodes"][1]["filters"][0]["group"] = 0
    canvas["nodes"][1]["filters"][1]["group"] = 1
    return canvas


def _widening_operator():
    canvas = real_case()
    canvas["nodes"][1]["filters"][1]["operator"] = "LIKE"
    return canvas


def _manipola(*, connected=True, as_source=False):
    nodes = [
        {"id": "src", "type": "project_source", "position": POS, "source_project_id": "estrai-1"},
        {"id": "f", "type": "transform", "position": POS, "transform_op": "filter",
         "config": {"column": "ac_id", "operator": "==", "value": ""}},
        _result(),
    ]
    edges = [{"id": "e1", "source": "src", "target": "f"}]
    if connected:
        edges.append({"id": "e2", "source": "f", "target": "out"})
    else:
        edges.append({"id": "e2", "source": "src", "target": "out"})
    if as_source:
        nodes.append({"id": "in_utente", "type": "project_input", "position": POS, "data": {
            "input_key": "utente", "label": "Utente che esegue", "input_kind": "current_user",
            "preview_username": "mario", "as_source": True, "source_column": "utente"}})
        if connected:
            edges.append({"id": "e3", "source": "in_utente", "target": "f"})
    else:
        nodes.append(_identity("f", "config.value"))
        edges.append(_binding_edge("in_utente", "f"))
    return {"nodes": nodes, "edges": edges}


def _arricchisci(*, connected=True):
    nodes = [
        {"id": "src", "type": "staticValues", "position": POS, "fields": [{"name": "a", "value": "1"}]},
        {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1", "static_parameters": {"utente": ""}},
        _result(),
        _identity("api", "static_parameters.utente"),
    ]
    edges = [{"id": "e1", "source": "src", "target": "api"}, _binding_edge("in_utente", "api")]
    edges.append({"id": "e2", "source": "api" if connected else "src", "target": "out"})
    return {"nodes": nodes, "edges": edges}


#: Ogni modo della raccolta (sezione B) in cui un binding resta senza effetto.
UNBOUND_CASES = {
    "B1_tabella_scollegata": (_disconnected_table, "estrai", "non arriva all'Output"),
    "B2_filtro_senza_colonna": (_empty_column_filter, "estrai", "non ha colonna"),
    "B3_gruppo_oppure": (_or_group, "estrai", "oppure"),
    "B4_operatore_che_allarga": (_widening_operator, "estrai", "LIKE"),
    "B5_passo_manipola_scollegato": (lambda: _manipola(connected=False), "manipola", "non arriva all'Output"),
    "B6_sorgente_utente_scollegata": (lambda: _manipola(connected=False, as_source=True), "manipola",
                                      "non arriva all'Output"),
    "B7_nodo_api_scollegato": (lambda: _arricchisci(connected=False), "arricchisci", "non arriva all'Output"),
}


@pytest.mark.parametrize("case", sorted(UNBOUND_CASES))
def test_each_unbound_mode_blocks_resolution(case):
    build, project_type, reason = UNBOUND_CASES[case]
    canvas = build()
    problems = _problems(canvas, project_type)
    assert problems and reason in problems[0].message, problems
    with pytest.raises(RuntimeIdentityError) as err:
        _resolve(canvas, project_type)
    assert err.value.code == UNBOUND


def test_real_case_message_names_table_and_column():
    message = _problems(_disconnected_table())[0].message
    assert message.startswith("Il filtro «Utente che esegue» (input «Utente che esegue») collegato a prt_cd.ac_id "
                              "non entra nella query: la tabella non arriva all'Output")


@pytest.mark.parametrize("case", ["B1_tabella_scollegata", "B2_filtro_senza_colonna", "B3_gruppo_oppure"])
def test_validate_reports_an_error_and_the_canvas_is_invalid(case, monkeypatch):
    monkeypatch.setattr(schema_execution_sources, "load_sql_options", lambda _cid: MSSQL)
    canvas = UNBOUND_CASES[case][0]()
    report = canvas_validation.validate_canvas(CanvasState(**canvas), project_id="p", project_type="estrai",
                                               execution_context={"default_connection_id": "conn"})
    found = [i for i in report.issues if i.code == UNBOUND]
    assert found and found[0].severity == "error" and found[0].node_id == "in_utente"
    assert report.has_errors


@pytest.mark.parametrize("case", ["B1_tabella_scollegata", "B2_filtro_senza_colonna"])
def test_query_preview_shows_the_error(case):
    canvas = UNBOUND_CASES[case][0]()
    described = describe_estrai_query(CanvasState(**canvas), project_id="p", project_type="estrai",
                                      default_connection_id="conn", load_sql_options=lambda _cid: MSSQL)
    assert described["error"] and "non entra nella query" in described["error"]


def _blocked(call):
    with pytest.raises(RuntimeIdentityError) as err:
        call()
    assert err.value.code == UNBOUND, err.value.message


def _version(canvas, project_type="estrai"):
    manifest = compile_manifest(source_project_id="p", project_type=project_type, canvas=canvas,
                                default_connection_id="conn")
    return SimpleNamespace(id="v1", canvas=canvas), manifest


def test_every_execution_path_blocks(monkeypatch):
    """(iii, a runtime) ogni percorso di esecuzione con «Utente che esegue»
    si blocca sul caso scollegato, prima di ogni lettura e di ogni file."""
    canvas = _disconnected_table()
    contract = _contract(canvas)
    # Avvio e stima di un Eseguibile (Estrai, Manipola, Arricchisci).
    version, manifest = _version(canvas)
    _blocked(lambda: upstream_plan.plan_for_version(version=version, manifest=manifest, group_id="g",
                                                    raw_inputs={}, identity=MARIO))
    # «Esegui di prova» (identita' di prova dal canvas salvato).
    _blocked(lambda: apply_test_run_inputs(contract, {}))
    # Valida con input (identita' di prova).
    _blocked(lambda: apply_project_runtime_inputs(contract, {}, identity=RuntimeIdentity.preview_of(canvas)))
    # Anteprima dell'Output e valori distinti.
    _blocked(lambda: apply_preview_inputs(contract))
    _blocked(lambda: apply_identity_inputs(contract, RuntimeIdentity.preview_of(canvas)))
    # Ricalcolo isolato a monte (piano dell'avvio di un Manipola).
    entry = SimpleNamespace(project=SimpleNamespace(project_type="estrai", canvas=canvas, name="Estrai",
                                                    default_connection_id="conn", group_id="g"))
    _blocked(lambda: upstream_plan._resolve_entry("estrai-1", entry, identity=MARIO, values={}))
    # Avvio dell'Eseguibile: la stessa risoluzione.
    _blocked(lambda: resolve_runtime_inputs(eseguibile_id="p", canvas=canvas, manifest=manifest, values={},
                                            group_id="g", identity=MARIO))


def test_step_preview_checks_only_the_step():
    """Anteprima di un passo: un binding fuori dal passo non lo riguarda; un
    binding dentro il passo senza effetto blocca."""
    canvas = real_case()
    assert _problems(canvas, node_id="val") == []
    broken = _empty_column_filter()
    assert _problems(broken, node_id="cd") and "non ha colonna" in _problems(broken, node_id="cd")[0].message


def test_canvas_without_output_is_left_to_the_executor():
    """Senza Output il canvas resta com'e' (``run_canvas``): nessun blocco
    di flusso; l'esecutore rifiuta il canvas con il suo messaggio."""
    canvas = _arricchisci(connected=False)
    canvas["nodes"] = [n for n in canvas["nodes"] if n["type"] != "result"]
    canvas["edges"] = [e for e in canvas["edges"] if e["target"] != "out"]
    assert _problems(canvas, "arricchisci") == []


def test_missing_moved_or_deleted_filter_index_is_refused_at_compile():
    """Binding a un indice di filtro inesistente o spostato su un filtro
    senza valore (filtro cancellato dopo il binding): la regola dei
    bersagli (``binding_capabilities``) lo rifiuta in Valida, pubblicazione
    e risoluzione."""
    beyond = real_case()
    beyond["nodes"][3]["data"]["bindings"][0]["target_field"] = "filters.5.value"
    moved = real_case()
    moved["nodes"][3]["data"]["bindings"][0]["target_field"] = "filters.0.value"  # IS NULL: nessun valore
    gone = real_case()
    gone["nodes"][3]["data"]["bindings"][0]["target_node_id"] = "cancellato"
    for canvas in (beyond, moved, gone):
        with pytest.raises(ValueError):
            _manifest(canvas)


def test_node_replaced_by_a_file_blocks():
    """Sorgente sostituita da un file: il binding dell'utente sul nodo
    sostituito non ha effetto."""
    canvas = real_case()
    manifest = _manifest(canvas).model_copy(update={
        "inputs": [*_manifest(canvas).inputs, ExecutableInputDefinition(key="file", label="File", kind="file",
                                                                         required=False)],
        "bindings": [*_manifest(canvas).bindings,
                     ExecutableInputBinding(input_key="file", target_node_id="cd", mode="data_source")],
    })
    assert identity_binding_problems(canvas, manifest) == []
    problems = identity_binding_problems(canvas, manifest, values={"file": {"file_id": "f1"}})
    assert problems and "sostituito dal file" in problems[0].message
    with pytest.raises(RuntimeIdentityError):
        assert_identity_bindings_effective(canvas, manifest, values={"file": {"file_id": "f1"}})


def _having(operator):
    canvas = {"nodes": [
        _table("root", "prt_cd", ["ac_id", "cd_id"]),
        _result(group_by_enabled=True, group_by_columns=["ac_id"],
                aggregations=[{"column": "*", "function": "COUNT", "alias": "n"}],
                having_enabled=True,
                having_conditions=[{"function": "COUNT", "column": "*", "operator": operator, "value": "1"}]),
        {**_identity("out", "having_conditions.0.value")}],
        "edges": [_out("root"), _binding_edge("in_utente", "out")]}
    return canvas


def test_having_binding_is_bound_or_refused():
    """B13: HAVING collegato e attivo -> il valore entra nella query; HAVING
    spento -> il bersaglio non e' offerto e il binding si rifiuta. Una
    condizione che la query salterebbe (operatore o funzione fuori elenco)
    non passa il modello del canvas."""
    canvas = _having(">")
    assert _problems(canvas) == []
    info = _executed_sql(_resolve(canvas)[0])
    assert "HAVING COUNT(*) > :p_0" in " ".join(info["sql"].split()) and info["params"]["p_0"] == "mario"
    off = _having(">")
    off["nodes"][1]["having_enabled"] = False
    with pytest.raises(ValueError):
        _manifest(off)
    with pytest.raises(ValueError):
        CanvasState(**_having("LIKE"))


def test_publication_refuses_an_unbound_identity_filter(monkeypatch):
    """C10: un Eseguibile con un filtro per utente senza effetto non si
    pubblica (prima di qualunque scrittura)."""
    from app.core.eseguibili import db as eseguibili_db, service
    from app.core.projects import access_context

    project = SimpleNamespace(project_type="estrai", group_id="g", canvas=_disconnected_table(),
                              default_connection_id="conn")
    monkeypatch.setattr(access_context, "load_project_publish_context", lambda _pid: project)
    created = MagicMock()
    monkeypatch.setattr(eseguibili_db, "create_eseguibile", created)
    _blocked(lambda: service.create_from_project("p", "Eseguibile", "g", "u"))
    created.assert_not_called()


def test_executor_defense_refuses_a_literal_filter_outside_the_query():
    """Difesa nell'esecutore: un filtro ``value_literal`` di una tabella che
    la query non contiene (canvas arrivato senza la verifica) -> la query
    non parte, anche con le opzioni reali della connessione."""
    canvas = CanvasState(**_disconnected_table())
    cd = next(n for n in canvas.nodes if n.id == "cd")
    cd.filters[1].value, cd.filters[1].value_literal = "mario", True
    result = query_plan.estrai_result_node(canvas.nodes)
    with pytest.raises(ValueError, match="prt_cd.ac_id non entra nella query"):
        query_plan.sql_query(QueryGenerator(), canvas.nodes, canvas.edges, result, MSSQL)


def test_feature_service_read_refuses_literal_filters_of_sql_tables():
    canvas = CanvasState(**{"nodes": [
        {"id": "fl", "type": "feature_source", "position": POS, "layer_url": "https://x/0"},
        _table("cd", "prt_cd", ["ac_id"], filters=[{"column": "ac_id", "operator": "=", "value": "mario",
                                                    "value_literal": True}]),
        _result()], "edges": [_out("fl")]})
    assert query_plan.feature_service_unbound_literal_filters(canvas.nodes) == ["prt_cd.ac_id"]
    executor = EstraiExecutor(MagicMock(), MagicMock(), QueryGenerator(), MagicMock(), features_service=MagicMock())
    result = executor._execute_feature_service("p", canvas, canvas.nodes[-1], SimpleNamespace(id="c"))
    assert result["success"] is False and "non entra nella query" in result["message"]
    executor.features_service.execute_query.assert_not_called()


# ------------------------------- non regressione: Eseguibili in produzione


@pytest.mark.parametrize("table", ["prt_cd", "ls", "cost_tran_recur", "cost_tran", "project", "wr"])
def test_root_filtered_estrai_like_production_still_runs(table):
    """«immobili attivi del mio istituto» (prt_cd radice, filtro ac_id) e gli
    Estrai a monte dei Manipola in produzione (filtro sulla radice): nessun
    blocco, il valore entra nella query."""
    canvas = {"nodes": [
        _table("root", table, ["id", "ac_id", "date_end"], filters=[
            {"column": "date_end", "operator": "IS NULL", "value": ""},
            {"column": "ac_id", "operator": "=", "value": ""}]),
        _result(), _identity("root", "filters.1.value", identity_transform=None)],
        "edges": [_out("root"), _binding_edge("in_utente", "root")]}
    assert _problems(canvas) == []
    resolved = _resolve(canvas)[0]
    info = _executed_sql(resolved)
    assert f"{table}.ac_id = :p_0" in info["sql"] and info["params"]["p_0"] == "mario"
    # Come progetto a monte di un Manipola (ricalcolo isolato).
    entry = SimpleNamespace(project=SimpleNamespace(project_type="estrai", canvas=canvas, name=table,
                                                    default_connection_id="conn", group_id="g"))
    resolved_upstream, _summary = upstream_plan._resolve_entry(f"estrai-{table}", entry, identity=MARIO, values={})
    assert _executed_sql(resolved_upstream["canvas"])["params"]["p_0"] == "mario"


def test_root_filter_with_joined_tables_still_runs():
    """Radice filtrata con tabelle collegate (anche nascoste) in tutti e due
    i versi (SC_A_patrimonio_unita_AMM e simili)."""
    canvas = real_case(direction="backward")
    canvas["nodes"][1]["filters"] = []
    canvas["nodes"][0]["filters"] = [{"column": "valore", "operator": "=", "value": ""}]
    canvas["nodes"][3]["data"]["bindings"] = [{"target_node_id": "val", "target_field": "filters.0.value"}]
    canvas["edges"][2] = _binding_edge("in_utente", "val")
    assert _problems(canvas) == []
    info = _executed_sql(_resolve(canvas)[0])
    assert "prt_cd_valuation.valore = :p_0" in info["sql"]


@pytest.mark.parametrize("build,project_type", [
    (lambda: _manipola(connected=True), "manipola"),
    (lambda: _manipola(connected=True, as_source=True), "manipola"),
    (lambda: _arricchisci(connected=True), "arricchisci"),
])
def test_connected_manipola_and_api_inputs_still_run(build, project_type):
    canvas = build()
    assert _problems(canvas, project_type) == []
    _resolve(canvas, project_type)


def test_real_case_is_now_valid_and_runs():
    canvas = real_case()
    assert _problems(canvas) == []
    version, manifest = _version(canvas)
    own, _plan = upstream_plan.plan_for_version(version=version, manifest=manifest, group_id="g",
                                                raw_inputs={}, identity=MARIO)
    assert own == {}


# ------------------- (iii) enumerazione: ogni percorso passa dalla verifica


def _functions(path: Path) -> dict[str, ast.FunctionDef]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _call_lines(func: ast.AST, name: str) -> list[int]:
    return [
        node.lineno for node in ast.walk(func)
        if isinstance(node, ast.Call) and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name))
    ]


#: Chi scrive il valore dell'utente nel canvas, o lo calcola: tutti passano
#: dalla verifica (``assert_identity_bindings_effective``) PRIMA.
IDENTITY_WRITERS = {
    # Scrivono il valore nel canvas (binding e sorgente «Utente che esegue»).
    "_resolve_into": {"app/core/eseguibili/runtime.py::resolve_runtime_inputs"},
    "_as_identity_source": {"app/core/eseguibili/runtime.py::_resolve_into"},
    "_apply_scalar_binding": {
        "app/core/eseguibili/runtime.py::_resolve_into",
        "app/core/eseguibili/binding_write.py::_apply_scalar_binding",  # segno value_literal
        "app/core/projects/identity_effect.py::identity_binding_problems",  # segnaposto della verifica
        "app/core/projects/query_preview.py::_bind_runtime_inputs",  # segnaposto dell'anteprima
    },
    # Calcolano il valore: la risoluzione, oppure l'impronta della stima e
    # dell'avvio dopo ``plan_for_version`` (che verifica per primo).
    "identity_values": {
        "app/core/eseguibili/runtime.py::resolve_runtime_inputs",
        "app/core/eseguibili/preflight.py::fingerprint_for",
        "app/core/eseguibili/preflight.py::preflight_versioned",
    },
    "resolve_identity_inputs": {"app/core/eseguibili/identity_transform.py::identity_values"},
}


@pytest.mark.parametrize("writer", sorted(IDENTITY_WRITERS))
def test_identity_values_are_written_only_behind_the_check(writer):
    """Un nuovo percorso che scrive o calcola il valore dell'utente senza
    passare da qui fa fallire il test (enumerazione AST di app e workers)."""
    assert _calls_in_functions({writer}) == IDENTITY_WRITERS[writer]


def test_resolution_and_plan_check_before_writing():
    runtime = _functions(BACKEND / "app/core/eseguibili/runtime.py")["resolve_runtime_inputs"]
    check = _call_lines(runtime, "assert_identity_bindings_effective")
    assert check and min(check) < min(_call_lines(runtime, "_resolve_into"))
    plan = _functions(BACKEND / "app/core/eseguibili/upstream_plan.py")["plan_for_version"]
    assert _call_lines(plan, "assert_identity_bindings_effective")
    assert min(_call_lines(plan, "assert_identity_bindings_effective")) < min(_call_lines(plan, "executable_closure"))
    preflight = _functions(BACKEND / "app/core/eseguibili/preflight.py")
    for name in ("fingerprint_for", "preflight_versioned"):
        assert min(_call_lines(preflight[name], "plan_for_version")) < min(_call_lines(preflight[name], "identity_values"))


def test_every_resolution_caller_is_an_execution_path_of_the_table():
    """I chiamanti della risoluzione: ognuno e' un percorso di
    :func:`test_every_execution_path_blocks` (nuovo chiamante -> nuova
    riga qui e nel test a runtime)."""
    assert _calls_in_functions({"resolve_runtime_inputs"}) == {
        "app/core/eseguibili/service.py::execute_versioned",
        "app/core/eseguibili/preflight.py::preflight_versioned",
        "app/core/eseguibili/upstream_plan.py::_resolve_entry",
        "app/core/orchestrator/runtime_inputs.py::_apply_own_runtime_inputs",
        "app/core/orchestrator/runtime_inputs.py::apply_identity_inputs",
    }
