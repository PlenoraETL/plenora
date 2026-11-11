"""«Utente che esegue» negli Estrai a monte (10/11/2026).

Regola: ``app/shared/execution/runtime_identity``, sezione «UTENTE CHE ESEGUE
NEGLI ESTRAI A MONTE» (U1-U5); raccolta
``docs/audits/identita-a-monte-raccolta-2026-11-10.md``. Ogni test qui
fallisce senza la funzione:

1. chiusura transitiva (diretta, indiretta, cicli, altro gruppo);
2. porta unica ``route_upstream_source``: standard / isolated / blocco;
3. piano all'avvio: stessa identita', trasformazione e valore scelto del
   nodo a monte, blocchi (nessuna identita', Arricchisci);
4. lettore Manipola: ricalcolo isolato in memoria, mai il risultato salvato,
   mai un ricalcolo che scrive; blocco senza piano;
5. orchestratore: ricalcolo isolato senza scritture;
6. contratto, prova, anteprima, snapshot della pubblicazione;
7. lettori che bloccano (Arricchisci, valori distinti, richiesta di stima);
8. enumerazione: ogni lettura di un progetto a monte passa dalla porta.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.eseguibili import upstream_plan
from app.core.eseguibili.output_persistence import MemoryOutputPersistence
from app.core.manipola import project_source_runtime as psr
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator import runtime_inputs
from app.core.projects import upstream_identity
from app.core.projects.canvas_models import CanvasState
from app.core.projects.upstream_identity import UpstreamProject
from app.shared import error_codes
from app.shared.execution.runtime_identity import (
    ISOLATED_UPSTREAM_RUN_FIELD,
    ISOLATED_UPSTREAMS_FIELD,
    IsolatedUpstreamRecomputeRefused,
    RuntimeIdentity,
    RuntimeIdentityError,
    UpstreamIdentityNeed,
    guard_upstream_recompute,
    route_upstream_source,
    upstream_input_key,
)
from tests2.eseguibili.unit.test_runtime_identity_inputs import POS, _estrai_canvas
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract]

REQUIRED = error_codes.RUNTIME_IDENTITY_REQUIRED
CLIENT = error_codes.RUNTIME_IDENTITY_CLIENT_VALUE
GROUP = "grp-1"
RULE = "'dmi_' + utente"


def _source(pid: str, node_id: str = "ps") -> dict:
    return {"id": node_id, "type": "project_source", "position": POS, "source_project_id": pid,
            "columns": [{"name": "owner", "type": "str", "visible": True}]}


def _reader(*pids: str) -> dict:
    nodes = [_source(pid, f"ps{i}") for i, pid in enumerate(pids)]
    nodes.append({"id": "out", "type": "result", "position": POS, "fileName": "m", "format": "csv"})
    edges = [{"id": f"e{i}", "source": f"ps{i}", "target": "out"} for i in range(len(pids))]
    return {"nodes": nodes, "edges": edges}


def _plain_estrai() -> dict:
    canvas = _estrai_canvas()
    canvas["nodes"] = [n for n in canvas["nodes"] if n["type"] != "project_input"]
    canvas["edges"] = [e for e in canvas["edges"] if e["source"] != "utente"]
    return canvas


class _Projects:
    """Progetti a monte del test (lettura della chiusura)."""

    def __init__(self, **projects: tuple[str, dict]) -> None:
        self.projects = projects
        self.reads: list[str] = []

    def __call__(self, project_id: str, *, group_id: str | None = None):
        self.reads.append(project_id)
        if project_id not in self.projects:
            return None
        project_type, canvas = self.projects[project_id]
        return UpstreamProject(project_id, f"Progetto {project_id}", project_type, GROUP,
                               copy.deepcopy(canvas), "conn-1")


@pytest.fixture
def projects(monkeypatch):
    def install(**items):
        loader = _Projects(**items)
        monkeypatch.setattr(upstream_identity, "load_upstream_project", loader)
        return loader
    return install


def _admin(role: str = "admin", username: str = "E0041") -> RuntimeIdentity:
    return RuntimeIdentity.authenticated(build_current_user(username=username, role=role), owner_group_id=GROUP)


def _published(canvas: dict, project_type: str, snapshot=None) -> dict:
    """Versione pubblicata e manifest per ``plan_for_version`` (snapshot
    presente: nessun backfill)."""
    version = SimpleNamespace(id=None, canvas=canvas, manifest={"upstream_identity": list(snapshot or [])})
    manifest = SimpleNamespace(project_type=project_type, upstream_identity=list(snapshot or []), inputs=[], bindings=[])
    return {"version": version, "manifest": manifest}


def _filter_value(canvas: dict):
    return next(n for n in canvas["nodes"] if n["id"] == "t")["filters"][0]


# ---------------------------------------------------------------- 1. chiusura


def test_closure_direct_indirect_and_plain(projects):
    projects(
        e1=("estrai", _estrai_canvas()),
        e2=("estrai", _plain_estrai()),
        m2=("manipola", _reader("e1")),
    )
    closure = upstream_identity.identity_closure(_reader("e2", "m2"), group_id=GROUP)
    assert list(closure) == ["m2", "e1"]
    assert closure["m2"].need.via == ("Progetto m2", "Progetto e1")
    assert closure["m2"].identity_nodes == ()
    assert closure["e1"].need.input_label == "Utente"
    assert upstream_identity.source_identity_need("e2", group_id=GROUP) is None
    assert upstream_identity.source_identity_need("m2", group_id=GROUP).via[-1] == "Progetto e1"


def test_closure_ignores_cycles_and_other_groups(projects):
    projects(a=("manipola", _reader("b")), b=("manipola", _reader("a", "fuori")))
    assert upstream_identity.identity_closure(_reader("a"), group_id=GROUP) == {}
    assert upstream_identity.source_identity_need("fuori", group_id=GROUP) is None


def test_closure_reads_each_project_once(projects):
    loader = projects(e1=("estrai", _estrai_canvas()), m2=("manipola", _reader("e1")), m3=("manipola", _reader("e1")))
    upstream_identity.identity_closure(_reader("m2", "m3", "e1"), group_id=GROUP)
    assert sorted(loader.reads) == ["e1", "m2", "m3"]


# ---------------------------------------------------------------- 2. porta


NEED = UpstreamIdentityNeed("e1", "Estrai sedi", "estrai", ("Estrai sedi",), "Utente")


def test_gate_routes_or_blocks():
    assert route_upstream_source(source_project_id="e1", need=None, isolated_upstreams=None) == "standard"
    assert route_upstream_source(source_project_id="e1", need=NEED, isolated_upstreams={"e1": {}}) == "isolated"
    for plan in (None, {}, {"altro": {}}):
        with pytest.raises(RuntimeIdentityError) as err:
            route_upstream_source(source_project_id="e1", need=NEED, isolated_upstreams=plan)
        assert err.value.code == REQUIRED
        assert "risultato salvato non si usa mai" in err.value.message
        assert "«Estrai sedi»" in err.value.message


def test_inside_an_isolated_run_nothing_recomputes_with_writes():
    with pytest.raises(IsolatedUpstreamRecomputeRefused) as err:
        guard_upstream_recompute(test_run=False, isolated=True, source_name="Altro")
    assert "Esegui prima il progetto a monte «Altro»" in str(err.value)
    guard_upstream_recompute(test_run=False, source_name="Altro")  # fuori: come prima


# ---------------------------------------------------------------- 3. piano


def test_plan_uses_the_same_identity_and_the_upstream_transform(projects):
    projects(e1=("estrai", _estrai_canvas(identity_transform=RULE)), e2=("estrai", _plain_estrai()))
    plan = upstream_plan.plan_isolated_upstreams(
        canvas=_reader("e1", "e2"), reader_type="manipola", group_id=GROUP, identity=_admin("esecutore"))
    assert list(plan.isolated) == ["e1"]  # e2 senza identita': risultato salvato
    entry = plan.isolated["e1"]
    assert entry["project_type"] == "estrai" and entry["default_connection_id"] == "conn-1"
    assert _filter_value(entry["canvas"])["value"] == "dmi_E0041"
    assert _filter_value(entry["canvas"])["value_literal"] is True  # solo un dato, mai SQL
    assert not [n for n in entry["canvas"]["nodes"] if n["type"] == "project_input"]
    assert plan.summary["e1"]["inputs"]["utente"]["username"] == "E0041"


def test_plan_is_transitive(projects):
    projects(e1=("estrai", _estrai_canvas()), m2=("manipola", _reader("e1")))
    plan = upstream_plan.plan_isolated_upstreams(
        canvas=_reader("m2"), reader_type="manipola", group_id=GROUP, identity=_admin("esecutore"))
    assert set(plan.isolated) == {"m2", "e1"}
    assert plan.isolated["m2"]["project_type"] == "manipola"


@pytest.mark.parametrize("role", ["admin", "superadmin"])
def test_an_admin_chooses_the_upstream_value_with_its_key(projects, role):
    projects(e1=("estrai", _estrai_canvas(identity_transform=RULE, admin_override=True)))
    own, plan = upstream_plan.plan_for_version(
        **_published(_reader("e1"), "manipola"),
        group_id=GROUP, raw_inputs={upstream_input_key("e1", "utente"): " 004_IS "}, identity=_admin(role))
    assert own == {}
    assert _filter_value(plan.isolated["e1"]["canvas"])["value"] == "004_IS"
    assert plan.summary["e1"]["inputs"]["utente"]["overridden_by"] == "E0041"


@pytest.mark.parametrize("role,override", [("esecutore", True), ("user", True), ("admin", False)])
def test_who_cannot_choose_is_refused(projects, role, override):
    projects(e1=("estrai", _estrai_canvas(admin_override=override)))
    with pytest.raises(RuntimeIdentityError) as err:
        upstream_plan.plan_isolated_upstreams(
            canvas=_reader("e1"), reader_type="manipola", group_id=GROUP, identity=_admin(role),
            upstream_values={"e1": {"utente": "004_IS"}})
    assert err.value.code == CLIENT


def test_an_admin_of_another_group_cannot_choose(projects):
    projects(e1=("estrai", _estrai_canvas(admin_override=True)))
    other = RuntimeIdentity.authenticated(build_current_user(username="X", role="admin"), owner_group_id="altro")
    with pytest.raises(RuntimeIdentityError) as err:
        upstream_plan.plan_isolated_upstreams(
            canvas=_reader("e1"), reader_type="manipola", group_id=GROUP, identity=other,
            upstream_values={"e1": {"utente": "004_IS"}})
    assert err.value.code == CLIENT


def test_unknown_upstream_keys_are_refused(projects):
    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(ValueError, match="Input non dichiarati: e1::altro"):
        upstream_plan.plan_isolated_upstreams(
            canvas=_reader("e1"), reader_type="manipola", group_id=GROUP, identity=_admin(),
            upstream_values={"e1": {"altro": "x"}})


@pytest.mark.parametrize("identity", [RuntimeIdentity.project_run(), RuntimeIdentity.unattended()])
def test_without_an_identity_the_plan_blocks(projects, identity):
    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(RuntimeIdentityError) as err:
        upstream_plan.plan_isolated_upstreams(canvas=_reader("e1"), reader_type="manipola", group_id=GROUP,
                                              identity=identity)
    assert err.value.code == REQUIRED and "«Progetto e1»" in err.value.message


def test_arricchisci_is_not_isolated(projects):
    projects(e1=("estrai", _estrai_canvas()), a1=("arricchisci", _reader("e1")))
    with pytest.raises(RuntimeIdentityError, match="Arricchisci non ricalcola"):
        upstream_plan.plan_isolated_upstreams(canvas=_reader("e1"), reader_type="arricchisci", group_id=GROUP,
                                              identity=_admin())
    with pytest.raises(RuntimeIdentityError, match="solo Estrai e Manipola"):
        upstream_plan.plan_isolated_upstreams(canvas=_reader("a1"), reader_type="manipola", group_id=GROUP,
                                              identity=_admin())


def test_no_upstream_identity_means_an_empty_plan(projects):
    projects(e2=("estrai", _plain_estrai()))
    plan = upstream_plan.plan_isolated_upstreams(canvas=_reader("e2"), reader_type="manipola", group_id=GROUP,
                                                 identity=RuntimeIdentity.project_run())
    assert plan.isolated == {} and plan.summary == {}


# ---------------------------------------------------------------- 4. lettore Manipola


def _canvas_state(canvas: dict) -> CanvasState:
    return CanvasState.model_validate(canvas)


def _spy_loaders(monkeypatch):
    calls = {"latest": 0, "dispatch": 0, "loaded": []}

    def latest(*a, **k):
        calls["latest"] += 1
        raise AssertionError("il risultato salvato non si legge")

    def loaded(materialized, node, source_project_id, group_id, dataframes):
        calls["loaded"].append((source_project_id, materialized.format, materialized.data))
        dataframes[node.id] = pd.DataFrame({"owner": ["x"]})

    monkeypatch.setattr(psr, "_materialize_latest_project_source", latest)
    monkeypatch.setattr(psr, "_load_materialized_project_source", loaded)
    monkeypatch.setattr(psr, "get_project_source_freshness", lambda *a, **k: None)
    return calls


def test_manipola_reader_recomputes_isolated_in_memory(projects, monkeypatch):
    projects(e1=("estrai", _estrai_canvas()))
    calls = _spy_loaders(monkeypatch)
    seen = {}

    def isolated(pid, entry, **kwargs):
        seen.update(pid=pid, entry=entry, **kwargs)
        return SimpleNamespace(success=True), ("csv", b"owner\nE0041\n")

    def dispatcher(*a, **k):
        raise AssertionError("mai un ricalcolo che scrive")

    frames = {}
    err = psr.load_project_sources(
        _canvas_state(_reader("e1")), frames, force_recalculate=True, pipeline_dispatcher=dispatcher,
        group_id=GROUP, isolated_upstreams={"e1": {"name": "E", "project_type": "estrai", "canvas": {}}},
        isolated_dispatcher=isolated,
    )
    assert err is None
    assert seen["pid"] == "e1" and seen["group_id"] == GROUP and seen["test_run"] is False
    assert calls["latest"] == 0
    assert calls["loaded"] == [("e1", "csv", b"owner\nE0041\n")]
    assert list(frames) == ["ps0"]


@pytest.mark.parametrize("force", [False, True])
def test_manipola_reader_without_a_plan_blocks(projects, monkeypatch, force):
    projects(e1=("estrai", _estrai_canvas()))
    calls = _spy_loaders(monkeypatch)
    err = psr.load_project_sources(
        _canvas_state(_reader("e1")), {}, force_recalculate=force,
        pipeline_dispatcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ricalcolo")),
        group_id=GROUP,
    )
    assert err["success"] is False and err["error"] == REQUIRED
    assert calls["latest"] == 0


def test_inside_an_isolated_run_a_stale_source_stops(projects, monkeypatch):
    projects(e2=("estrai", _plain_estrai()))
    monkeypatch.setattr(psr, "get_project_source_freshness", lambda *a, **k: SimpleNamespace(
        stale=True, message="vecchio", source_project_name="Plain"))
    err = psr.load_project_sources(
        _canvas_state(_reader("e2")), {}, force_recalculate=True,
        pipeline_dispatcher=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ricalcolo")),
        group_id=GROUP, isolated_run=True,
    )
    assert err["user_error"] is True and "«Plain»" in err["message"]


def test_isolated_failure_is_reported(projects, monkeypatch):
    projects(e1=("estrai", _estrai_canvas()))
    _spy_loaders(monkeypatch)
    err = psr.load_project_sources(
        _canvas_state(_reader("e1")), {}, force_recalculate=False, pipeline_dispatcher=None, group_id=GROUP,
        isolated_upstreams={"e1": {"name": "Sedi"}},
        isolated_dispatcher=lambda *a, **k: (SimpleNamespace(success=False, message="query rotta"), None),
    )
    assert "«Sedi»" in err["message"] and "query rotta" in err["message"]


# ---------------------------------------------------------------- 5. orchestratore


def test_isolated_upstream_writes_nothing():
    from app.core.orchestrator.service import OrchestratorService

    seen = {}

    def estrai_execute(project_id, canvas, run_id=None, intermediate_mode_override=None,
                       execution_context=None, output_persistence=None):
        seen.update(run_id=run_id, context=dict(execution_context), persistence=output_persistence)
        output_persistence.persist_dataframe_output(
            df=pd.DataFrame({"owner": ["E0041"]}), output_format="csv", filename_stem="o")
        return {"success": True, "rows_count": 1, "output_path": "o.csv"}

    estrai = MagicMock()
    estrai.execute.side_effect = estrai_execute
    service = OrchestratorService(MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                                  estrai_executor=estrai, manipola_executor=MagicMock(), enrich_executor=MagicMock())
    service._execute_carica_if_present = MagicMock(side_effect=AssertionError("mai Carica"))
    resolved = copy.deepcopy(_plain_estrai())
    artifact, captured = service.execute_isolated_upstream(
        "e1", {"project_type": "estrai", "canvas": resolved, "default_connection_id": "conn-1"},
        group_id=GROUP, isolated_upstreams={"e1": {}},
    )
    assert artifact.success is True
    assert captured[0] == "csv" and b"E0041" in captured[1]
    assert seen["run_id"] is None  # nessuna run intermedia
    assert isinstance(seen["persistence"], MemoryOutputPersistence)  # nessuna riga results
    assert seen["context"]["persist_contract"] is False  # nessun output_contract
    assert seen["context"][ISOLATED_UPSTREAM_RUN_FIELD] is True
    assert "output_task_id" not in seen["context"]


def test_isolated_upstream_with_identity_still_unresolved_is_blocked():
    """Ultima difesa: un canvas del piano con il nodo ancora presente blocca."""
    from app.core.orchestrator.service import OrchestratorService

    service = OrchestratorService(MagicMock(), MagicMock(), MagicMock(), MagicMock(),
                                  estrai_executor=MagicMock(), manipola_executor=MagicMock(),
                                  enrich_executor=MagicMock())
    artifact, captured = service.execute_isolated_upstream(
        "e1", {"project_type": "estrai", "canvas": _estrai_canvas()}, group_id=GROUP, isolated_upstreams={})
    assert artifact.success is False and captured is None


def test_memory_persistence_never_touches_the_storage(monkeypatch):
    from app.core.eseguibili import storage as executable_storage

    monkeypatch.setattr(executable_storage, "ExecutableOutputWriter",
                        MagicMock(side_effect=AssertionError("storage")))
    persistence = MemoryOutputPersistence(group_id=GROUP)
    saved = persistence.persist_dataframe_output(df=pd.DataFrame({"a": [1]}), output_format="csv",
                                                 filename_stem="x")
    assert saved.storage_key == "memory://x.csv"
    assert persistence.captured[0] == "csv" and b"a" in persistence.captured[1]


# ---------------------------------------------------------------- 6. contratto, prova, anteprima, snapshot


def _contract(canvas: dict, **extra) -> PipelineExecutionContract:
    return PipelineExecutionContract(schema_version=2, project_id="m", project_type="manipola", group_id=GROUP,
                                     canvas=canvas, **extra)


def test_contract_carries_the_plan_to_the_executors():
    plan = {"e1": {"project_type": "estrai", "name": "E", "canvas": _plain_estrai()}}
    contract = _contract(_reader("e1"), isolated_upstreams=plan)
    again = PipelineExecutionContract.from_payload(contract.to_payload())
    assert again.isolated_upstreams == plan
    assert again.build_runtime_context()[ISOLATED_UPSTREAMS_FIELD] == plan
    # Mai dal runtime_context: lo scrive solo il server nel campo del contratto.
    sneaky = _contract(_reader("e1"), runtime_context={ISOLATED_UPSTREAMS_FIELD: plan,
                                                       ISOLATED_UPSTREAM_RUN_FIELD: True})
    context = sneaky.build_runtime_context()
    assert ISOLATED_UPSTREAMS_FIELD not in context and ISOLATED_UPSTREAM_RUN_FIELD not in context
    assert "isolated_upstreams" not in _contract(_reader("e1")).to_payload()


def test_test_run_of_a_manipola_without_its_own_input(projects):
    projects(e1=("estrai", _estrai_canvas(preview_username="E0071", identity_transform=RULE)))
    contract = runtime_inputs.apply_test_run_inputs(_contract(_reader("e1")), {})
    assert contract.test_run_subject["username"] == "E0071"
    assert _filter_value(contract.isolated_upstreams["e1"]["canvas"])["value"] == "dmi_E0071"


def test_test_run_needs_one_test_username(projects):
    projects(e1=("estrai", _estrai_canvas(preview_username="A")), e3=("estrai", _estrai_canvas(preview_username="B")))
    with pytest.raises(RuntimeIdentityError) as err:
        runtime_inputs.apply_test_run_inputs(_contract(_reader("e1", "e3")), {})
    assert err.value.code == REQUIRED
    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(RuntimeIdentityError, match="nome utente di prova"):
        runtime_inputs.apply_test_run_inputs(_contract(_reader("e1")), {})


def test_test_run_value_chosen_in_the_upstream_node(projects):
    projects(e1=("estrai", _estrai_canvas(preview_username="E0071", admin_override=True,
                                          preview_admin_value="004_IS")))
    contract = runtime_inputs.apply_test_run_inputs(_contract(_reader("e1")), {})
    assert _filter_value(contract.isolated_upstreams["e1"]["canvas"])["value"] == "004_IS"


def test_test_run_without_any_identity_is_not_applicable(projects):
    projects(e2=("estrai", _plain_estrai()))
    with pytest.raises(runtime_inputs.ProjectTestRunNotApplicable):
        runtime_inputs.apply_test_run_inputs(_contract(_reader("e2")), {})


def test_preview_plans_with_the_test_username(projects):
    projects(e1=("estrai", _estrai_canvas(preview_username="E0071")))
    contract = runtime_inputs.apply_preview_inputs(_contract(_reader("e1")))
    assert _filter_value(contract.isolated_upstreams["e1"]["canvas"])["value"] == "E0071"


def test_project_run_blocks_at_the_start(projects):
    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(RuntimeIdentityError) as err:
        runtime_inputs.apply_project_runtime_inputs(_contract(_reader("e1")), {}, identity=RuntimeIdentity.project_run())
    assert err.value.code == REQUIRED


def test_snapshot_blocks_when_an_upstream_input_is_removed(projects):
    projects(e1=("estrai", _estrai_canvas()))
    closure = upstream_identity.identity_closure(_reader("e1"), group_id=GROUP)
    snapshot = upstream_plan.upstream_snapshot(closure)
    assert [row.model_dump() for row in snapshot] == [
        {"project_id": "e1", "project_name": "Progetto e1", "input_key": "utente", "label": "Utente"}]
    upstream_plan.assert_upstream_snapshot(snapshot, closure)  # invariato: si avvia
    projects(e1=("estrai", _plain_estrai()))
    gone = upstream_identity.identity_closure(_reader("e1"), group_id=GROUP)
    with pytest.raises(RuntimeIdentityError, match="ripubblicarlo"):
        upstream_plan.assert_upstream_snapshot(snapshot, gone)
    rows = upstream_plan.upstream_identity_changes(upstream_plan.removed_upstream_inputs(snapshot, gone))
    assert [(r["change"], r["blocking"]) for r in rows] == [("upstream_identity_removed", True)]
    # Un input a monte AGGIUNTO dopo la pubblicazione restringe: nessuna riga.
    assert upstream_plan.upstream_identity_changes(upstream_plan.removed_upstream_inputs([], closure)) == []


def test_upstream_rows_for_the_run_window(projects):
    projects(e1=("estrai", _estrai_canvas(admin_override=True, admin_override_label="Istituto",
                                          identity_transform=RULE)))
    rows = upstream_identity.upstream_input_rows(upstream_identity.identity_closure(_reader("e1"), group_id=GROUP))
    assert rows == [{"key": "e1::utente", "input_key": "utente", "project_id": "e1",
                     "project_name": "Progetto e1", "label": "Utente", "kind": "current_user",
                     "transform": RULE, "admin_override": True, "admin_override_label": "Istituto"}]


def test_editor_annotation_is_transient(projects):
    projects(e1=("estrai", _estrai_canvas(preview_username="E0071")))
    canvas = _canvas_state(_reader("e1"))
    upstream_identity.annotate_canvas_upstream_identity(canvas, group_id=GROUP)
    note = canvas.nodes[0].data[upstream_identity.UPSTREAM_IDENTITY_ANNOTATION]
    assert note["inputs"][0]["preview_username"] == "E0071"
    upstream_identity.clear_canvas_upstream_identity(canvas)
    assert upstream_identity.UPSTREAM_IDENTITY_ANNOTATION not in canvas.nodes[0].data


# ---------------------------------------------------------------- 7. lettori che bloccano


def test_arricchisci_loader_blocks(projects):
    from app.core.arricchisci.api_connector import ApiConnectionService

    projects(e1=("estrai", _estrai_canvas()))
    ctx = SimpleNamespace(group_id=GROUP, test_run=False, preview_limit=None)
    with pytest.raises(RuntimeIdentityError) as err:
        ApiConnectionService()._load_source_project({"source_project_id": "e1"}, ctx=ctx)
    assert err.value.code == REQUIRED


def test_distinct_values_never_read_the_unfiltered_result(projects, monkeypatch):
    from app.core.artifacts import SourceLoader
    from app.core.manipola.canvas_data_preparation import CanvasDataPreparation

    projects(e1=("estrai", _estrai_canvas()))
    monkeypatch.setattr(SourceLoader, "load_in_memory", MagicMock(side_effect=AssertionError("salvato")))
    frame, _sampled, reason = CanvasDataPreparation(_canvas_state(_reader("e1")), GROUP).output("ps0")
    assert frame is None and "risultato salvato non si usa mai" in reason


def test_enrichment_request_loader_blocks(projects):
    from app.core.arricchisci.source_request_loader import load_request_data

    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(RuntimeIdentityError):
        load_request_data(SimpleNamespace(file_source_id=None, project_id="e1"),
                          build_current_user(group_id=GROUP))


# ---------------------------------------------------------------- 8. enumerazione


APP = Path(upstream_identity.__file__).resolve().parents[2]
BACKEND = APP.parent
# Correzioni minori (11/11/2026, A1): anche i worker (Carica, Pianificazioni).
ROOTS = (APP, BACKEND / "workers")
# Chiamate che leggono o ricalcolano un progetto (risultato salvato, ricalcolo
# a monte, ricalcolo isolato, artefatto di una run, download del risultato).
READ_CALLS = {
    "LatestProjectArtifactRef", "latest_project_artifact_ref_cls", "materialize_latest", "pipeline_dispatcher",
    "_recompute_upstream_project", "isolated_dispatcher", "execute_isolated_upstream", "load_in_memory",
    "resolve_project_file_bytes", "materialize_run", "open_result_stream", "project_result_response",
}
# ``SourceLoader().load(...)`` / ``source_loader_cls().load(...)``.
LOADER_FACTORIES = {"SourceLoader", "source_loader_cls"}
# Le porte (runtime_identity U1 e U6).
GATES = {"route_upstream_source", "route_saved_result", "assert_saved_result_available"}
GATED = "porta"  # la funzione chiama una porta PRIMA della prima lettura


@dataclass(frozen=True)
class AfterGate:
    """Lettura in una funzione chiamata SOLO da ``callers``, ognuno dei quali
    chiama la porta prima di chiamarla (verificato)."""

    callers: tuple[str, ...]


# Ogni sito: "porta", AfterGate(chiamanti) oppure il motivo per cui non e'
# una lettura del risultato di un progetto che potrebbe filtrare per utente.
# Un sito nuovo fa fallire il test.
UPSTREAM_READ_SITES = {
    "app/core/manipola/project_source_runtime.py:load_project_sources": GATED,
    "app/core/manipola/project_source_runtime.py:_materialize_latest_project_source": AfterGate((
        "app/core/manipola/project_source_runtime.py:load_project_sources",)),
    "app/core/manipola/project_source_runtime.py:_load_isolated_upstream": AfterGate((
        "app/core/manipola/project_source_runtime.py:load_project_sources",)),
    "app/core/arricchisci/api_connection_source_loading.py:_load_source_project": GATED,
    "app/core/arricchisci/api_connection_rest_file.py:_resolve_file_source_path": GATED,
    "app/core/arricchisci/source_request_loader.py:load_request_data": GATED,
    "app/core/manipola/canvas_data_preparation.py:_output": GATED,
    "app/core/artifacts/source_loader.py:_load_project": "infrastruttura del caricatore (chi la chiama passa la porta)",
    "app/core/artifacts/source_loader.py:load_in_memory": "infrastruttura del caricatore",
    "app/core/artifacts/source_loader.py:resolve_project_file_bytes": "infrastruttura del caricatore",
    "app/core/artifacts/types.py:from_node_data": "costruzione del riferimento, nessuna lettura",
    "app/core/carica/dispatch.py:maybe_multilayer_arcgis_write": AfterGate((
        "workers/tasks/carica_task.py:execute_carica_task",)),
    "workers/tasks/carica_task.py:_load_upstream_data": AfterGate((
        "workers/tasks/carica_task.py:execute_carica_task",)),
    "app/core/carica/conflict_request_loader.py:_load_project_conflict_dataframe": GATED,
    "app/core/features/routes.py:_resolve_input_dataframe": AfterGate((
        "app/core/features/routes.py:write_features", "app/core/features/routes.py:create_service")),
    "app/core/orchestrator/download_response.py:project_result_response": AfterGate((
        "app/core/orchestrator/router.py:download_project_result",)),
    "app/core/orchestrator/router.py:download_project_result": GATED,
    "app/core/orchestrator/router.py:download_task_result": GATED,
    "app/core/scheduler/router.py:_download_result_file": AfterGate((
        "app/core/scheduler/router.py:download_schedule_output",)),
    "app/core/arricchisci/api_connection_source_loading.py:_load_file_source": "file caricato, non un progetto",
    "app/core/arricchisci/api_connection_source_loading.py:_load_static_values": "valori statici, non un progetto",
    "app/core/scheduler/execution_manager.py:_execute_carica": (
        "artefatto della run appena calcolata dalla Pianificazione (la run passa dalla porta)"),
    "app/runtime/services.py:dispatch_isolated_upstream": "cablaggio del ramo «isolated»",
    "workers/tasks/pipeline_task.py:_persist_executable_output": (
        "output della PROPRIA esecuzione dell'Eseguibile (spazio dell'attivita')"),
}


@dataclass
class _Function:
    key: str
    source: str
    calls: list  # (riga, colonna, nome, lettura?)


def _call_name(node: ast.Call) -> str | None:
    f = node.func
    name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
    if name == "load" and isinstance(f, ast.Attribute) and isinstance(f.value, ast.Call):
        inner = f.value.func
        factory = inner.id if isinstance(inner, ast.Name) else inner.attr if isinstance(inner, ast.Attribute) else None
        return "SourceLoader().load" if factory in LOADER_FACTORIES else name
    return name


def _functions() -> dict[str, _Function]:
    found: dict[str, _Function] = {}
    for root in ROOTS:
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            rel = path.relative_to(BACKEND).as_posix()

            def visit(node, func=None):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func = _Function(f"{rel}:{node.name}", ast.get_source_segment(source, node) or "", [])
                    found.setdefault(func.key, func)
                if isinstance(node, ast.Call) and func is not None:
                    name = _call_name(node)
                    hit = name in READ_CALLS or name == "SourceLoader().load"
                    f = node.func
                    if name == "from_node_data" and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "SourceRef":
                        first = node.args[0] if node.args else None
                        hit = not (isinstance(first, ast.Constant) and first.value != "project_source")
                    func.calls.append((node.lineno, node.col_offset, name, hit))
                for child in ast.iter_child_nodes(node):
                    visit(child, func)

            visit(tree)
    return found


def _sites(functions: dict[str, _Function]) -> dict[str, _Function]:
    return {key: fn for key, fn in functions.items() if any(hit for *_pos, hit in fn.calls)}


def _first(fn: _Function, names=None, *, read: bool = False) -> tuple[int, int] | None:
    positions = [(line, col) for line, col, name, hit in fn.calls if (hit if read else name in names)]
    return min(positions) if positions else None


def test_every_upstream_read_passes_through_the_gate():
    """Ogni lettura (anche nei worker, anche via ``SourceLoader().load`` e
    ``materialize_run``) e' nella tabella; una voce «porta» chiama la porta
    PRIMA della prima lettura; una voce AfterGate e' letta solo dopo la porta
    in ognuno dei suoi chiamanti (verificato sul codice, non dichiarato)."""
    functions = _functions()
    sites = _sites(functions)
    assert sorted(sites) == sorted(UPSTREAM_READ_SITES), (
        "Nuova lettura di un progetto (risultato salvato o ricalcolo): passa dalla porta "
        "runtime_identity.route_upstream_source / route_saved_result e aggiungila a UPSTREAM_READ_SITES")
    for key, rule in UPSTREAM_READ_SITES.items():
        if rule == GATED:
            gate, read = _first(sites[key], GATES), _first(sites[key], read=True)
            assert gate is not None and gate < read, f"{key}: la porta deve precedere la lettura"
        elif isinstance(rule, AfterGate):
            site_name = key.rsplit(":", 1)[1]
            for caller_key in rule.callers:
                caller = functions[caller_key]
                gate = _first(caller, GATES)
                call = _first(caller, {site_name})
                assert call is not None, f"{caller_key} non chiama {site_name}"
                if caller_key in UPSTREAM_READ_SITES and isinstance(UPSTREAM_READ_SITES[caller_key], AfterGate):
                    continue  # il chiamante e' a sua volta dopo la porta (verificato alla sua voce)
                assert gate is not None and gate < call, f"{caller_key}: la porta deve precedere {site_name}"


def test_every_gate_call_reads_the_closure():
    """La porta riceve sempre il bisogno letto dalla chiusura viva."""
    for root in ROOTS:
        for path in root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "def route_upstream_source" in source:
                continue
            calls = source.count("route_upstream_source(") + source.count("route_saved_result(")
            if calls:
                assert calls == source.count("need=source_identity_need("), path


def test_carica_of_a_project_that_needs_the_identity_is_blocked_before_reading(projects, monkeypatch):
    """A1: il Carica di un Estrai con «Utente che esegue» (o di un Manipola
    che ne legge uno) si ferma col motivo, prima di leggere o ricalcolare."""
    from workers.tasks import carica_task

    projects(e1=("estrai", _estrai_canvas()), m1=("manipola", _reader("e1")))
    reads: list[str] = []
    monkeypatch.setattr(carica_task, "recheck_user_authorized", lambda **_k: None)
    monkeypatch.setattr(carica_task, "is_already_completed", lambda _rid: False)
    monkeypatch.setattr(carica_task.intermediate_runs_db, "get_run", lambda _rid: None)
    monkeypatch.setattr(carica_task, "open_run", lambda **_k: None)
    monkeypatch.setattr(carica_task, "set_run_context", lambda **_k: None)
    monkeypatch.setattr(carica_task, "close_run", lambda **_k: reads.append("close_run"))
    monkeypatch.setattr(carica_task, "_update_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(carica_task, "_close_worker_connections", lambda **_k: None)
    monkeypatch.setattr(carica_task, "_load_upstream_data", lambda *_a, **_k: reads.append("read"))
    monkeypatch.setattr(carica_task, "_acquire_target_lock_or_return_busy", lambda **_k: reads.append("lock"))
    monkeypatch.setattr(carica_task.ProjectService, "get_project_metadata",
                        lambda self, pid: SimpleNamespace(group_id=GROUP))
    shim = SimpleNamespace(request=SimpleNamespace(id="task-c"))
    for pid in ("e1", "m1"):
        reads.clear()
        result = carica_task.execute_carica_task(shim, pid, {"nodes": []}, {"type": "db"})
        assert result["success"] is False and result["error"] == REQUIRED
        assert "risultato salvato non si usa mai" in result["message"]
        assert reads == ["close_run"]  # nessun lock, nessuna lettura


@pytest.mark.parametrize("role,status", [("user", 409), ("viewer", 409), ("admin", 409), ("superadmin", None)])
def test_saved_result_of_a_project_that_filters_by_user_is_not_exposed(projects, role, status):
    """A1b: download e pubblicazione del risultato salvato: 409 col motivo,
    salvo il superadmin; un progetto che non filtra passa."""
    from fastapi import HTTPException

    projects(e1=("estrai", _estrai_canvas()), m1=("manipola", _reader("e1")), e2=("estrai", _plain_estrai()))
    user = build_current_user(role=role, group_id=GROUP)
    for pid in ("e1", "m1"):
        if status is None:
            upstream_identity.assert_saved_result_available(pid, user)
            continue
        with pytest.raises(HTTPException) as err:
            upstream_identity.assert_saved_result_available(pid, user)
        assert err.value.status_code == status
        assert "filtra per utente: usa l'Eseguibile" in err.value.detail["message"]
        assert err.value.detail["error"] == REQUIRED
    upstream_identity.assert_saved_result_available("e2", user)


# ---------------------------------------------------------------- 9. endpoint dell'Eseguibile


def _eseguibile(canvas: dict, *, project_type: str = "manipola", snapshot=None):
    from app.core.eseguibili import db as eseguibili_db
    from tests2.eseguibili.unit.test_runtime_identity_inputs import _manifest

    manifest = _manifest(project_type, canvas).model_dump()
    manifest["upstream_identity"] = snapshot or []
    version = eseguibili_db.EseguibileVersionRow(
        id="version-1", eseguibile_id="exe-1", version_number=1, schema_version=2, project_type=project_type,
        source_project_id="m", source_hash="h", canvas=canvas, manifest=manifest, status="published",
        created_by="u-1", created_at="2026-11-10T00:00:00+00:00")
    executable = eseguibili_db.EseguibileRow(
        id="exe-1", name="Archibus", description="", canvas=canvas, expected_columns=[],
        default_output_format="csv", group_id=GROUP, created_by="u-1",
        created_at="2026-11-10T00:00:00+00:00", updated_at="2026-11-10T00:00:00+00:00",
        project_type=project_type, active_version_id="version-1", manifest=manifest)
    return executable, version


def _endpoint(user, inputs, *, canvas=None, project_type="manipola", snapshot=None, path="runs"):
    from unittest.mock import patch

    from tests2.eseguibili.unit.conftest import build_client
    from tests2.eseguibili.unit.test_runtime_identity_inputs import _Patched

    executable, version = _eseguibile(canvas or _reader("e1"), project_type=project_type, snapshot=snapshot)
    patches = [
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value=executable),
        patch("app.core.eseguibili.router._check_visible", return_value=None),
        patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.service.eseguibili_db.get_active_eseguibile_version", return_value=version),
        patch("app.core.eseguibili.router._save_task_owner"),
        patch("app.core.eseguibili.router._task_service.reserve_task_id", return_value="task-1"),
        patch("app.core.eseguibili.service.eseguibili_db.bind_eseguibile_run_task"),
    ]
    task = SimpleNamespace(task_id="task-1")
    with _Patched(patches), \
         patch("app.core.eseguibili.service.eseguibili_db.create_eseguibile_run", return_value="run-1") as created, \
         patch("app.core.orchestrator.task_service.task_service.create_pipeline_task", return_value=task) as submit:
        response = build_client(user).post(f"/api/eseguibili/exe-1/{path}", json={"inputs": inputs})
    if response.status_code != 200:
        return response.status_code, response.json(), None
    if path != "runs":
        return 200, response.json(), None
    return 200, submit.call_args.kwargs["execution_contract"], created.call_args.kwargs["input_summary"]


def test_eseguibile_run_filters_the_upstream_estrai(projects):
    projects(e1=("estrai", _estrai_canvas(identity_transform=RULE)), e2=("estrai", _plain_estrai()))
    status, contract, summary = _endpoint(build_current_user(username="E0041", role="esecutore", group_id=GROUP),
                                          {}, canvas=_reader("e1", "e2"))
    assert status == 200
    assert list(contract["isolated_upstreams"]) == ["e1"]
    assert _filter_value(contract["isolated_upstreams"]["e1"]["canvas"])["value"] == "dmi_E0041"
    assert summary[upstream_plan.UPSTREAM_SUMMARY_KEY]["e1"]["inputs"]["utente"]["value"] == "dmi_E0041"


def test_eseguibile_admin_value_for_an_upstream_input(projects):
    projects(e1=("estrai", _estrai_canvas(admin_override=True)))
    admin = build_current_user(username="capo", role="admin", group_id=GROUP)
    status, contract, summary = _endpoint(admin, {"e1::utente": "004_IS"})
    assert status == 200
    assert _filter_value(contract["isolated_upstreams"]["e1"]["canvas"])["value"] == "004_IS"
    assert summary[upstream_plan.UPSTREAM_SUMMARY_KEY]["e1"]["inputs"]["utente"]["overridden_by"] == "capo"
    esecutore = build_current_user(username="E0041", role="esecutore", group_id=GROUP)
    status, body, _ = _endpoint(esecutore, {"e1::utente": "004_IS"})
    assert status == 400 and CLIENT in str(body)


def test_eseguibile_blocks_when_the_upstream_filter_was_removed(projects):
    projects(e1=("estrai", _plain_estrai()))
    snapshot = [{"project_id": "e1", "project_name": "Sedi", "input_key": "utente", "label": "Utente"}]
    user = build_current_user(username="E0041", role="esecutore", group_id=GROUP)
    for path in ("runs", "preflight"):
        status, body, _ = _endpoint(user, {}, snapshot=snapshot, path=path)
        assert status == 400 and "ripubblicarlo" in str(body)


def test_eseguibile_arricchisci_reading_an_identity_estrai_is_blocked(projects):
    # (L'endpoint di Arricchisci chiede prima il file del suo adattatore
    # legacy: qui il piano dell'avvio e della stima, la stessa funzione.)
    projects(e1=("estrai", _estrai_canvas()))
    with pytest.raises(RuntimeIdentityError, match="Arricchisci non ricalcola"):
        upstream_plan.plan_for_version(
            **_published(_reader("e1"), "arricchisci"),
            group_id=GROUP, raw_inputs={}, identity=_admin("esecutore"))


def test_preflight_fingerprint_follows_the_upstream(projects):
    user = build_current_user(username="E0041", role="esecutore", group_id=GROUP)
    projects(e1=("estrai", _estrai_canvas()))
    _s, first, _ = _endpoint(user, {}, path="preflight")
    projects(e1=("estrai", _estrai_canvas(identity_transform=RULE)))
    _s, second, _ = _endpoint(user, {}, path="preflight")
    assert first["preparation_fingerprint"] != second["preparation_fingerprint"]


# ---------------------------------------------------------------- 10. correzioni minori (11/11/2026)


def _unreached_reader(*reached: str, unreached: str) -> dict:
    """Manipola con un ramo scollegato dall'Output che legge ``unreached``."""
    canvas = _reader(*reached)
    canvas["nodes"].append(_source(unreached, "loose"))
    return canvas


def test_a5_closure_follows_only_what_the_run_reads(projects):
    """A5: un ramo che non arriva all'Output (run_canvas non lo esegue) non
    entra nella chiusura, ne' nel lettore ne' nei progetti a monte; il passo
    dell'anteprima segue i suoi antenati."""
    projects(e1=("estrai", _estrai_canvas()), e2=("estrai", _plain_estrai()),
             m2=("manipola", _unreached_reader("e2", unreached="e1")))
    assert upstream_identity.identity_closure(_unreached_reader("e2", unreached="e1"), group_id=GROUP) == {}
    assert upstream_identity.identity_closure(_reader("m2"), group_id=GROUP) == {}
    assert upstream_identity.source_identity_need("m2", group_id=GROUP) is None
    # Anteprima del passo scollegato: lo legge, quindi ne ha bisogno.
    closure = upstream_identity.identity_closure(_unreached_reader("e2", unreached="e1"), group_id=GROUP,
                                                 node_id="loose")
    assert list(closure) == ["e1"]
    # «Esegui» del Manipola con il ramo scollegato non si blocca piu'.
    runtime_inputs.apply_project_runtime_inputs(
        _contract(_unreached_reader("e2", unreached="e1")), {}, identity=RuntimeIdentity.project_run())


def test_a5_a_source_replaced_by_an_input_is_not_read(projects):
    """A5: la sorgente sostituita da un file dell'Eseguibile non si legge:
    obbligatorio sempre, facoltativo solo se il file c'e' all'avvio."""
    projects(e1=("estrai", _estrai_canvas()))
    file_input = SimpleNamespace(key="f", kind="file", required=True, default_value=None)
    binding = SimpleNamespace(input_key="f", mode="data_source", target_node_id="ps0")
    common = dict(canvas=_reader("e1"), project_type="manipola", group_id=GROUP, bindings=[binding])
    assert upstream_plan.executable_closure(**common, inputs=[file_input]) == {}
    optional = SimpleNamespace(key="f", kind="file", required=False, default_value=None)
    assert list(upstream_plan.executable_closure(**common, inputs=[optional])) == ["e1"]
    assert upstream_plan.executable_closure(**common, inputs=[optional], raw_inputs={"f": {"id": "x"}}) == {}
    assert list(upstream_plan.executable_closure(**common, inputs=[optional], raw_inputs={"f": []})) == ["e1"]


def test_a4_validate_with_inputs_reads_the_upstream_test_username(projects):
    """A4: Valida con input (e ogni identita' di prova dell'editor) legge lo
    username di prova anche dai progetti a monte."""
    projects(e1=("estrai", _estrai_canvas(preview_username="E0071")))
    identity, closure = runtime_inputs.editor_identity(_reader("e1"), group_id=GROUP)
    assert identity.origin == "preview" and identity.username == "E0071" and list(closure) == ["e1"]
    contract = runtime_inputs.apply_project_runtime_inputs(_contract(_reader("e1")), {}, identity=identity,
                                                           closure=closure)
    assert _filter_value(contract.isolated_upstreams["e1"]["canvas"])["value"] == "E0071"
    test_identity, _ = runtime_inputs.editor_identity(_reader("e1"), group_id=GROUP, origin="test_run")
    assert test_identity.origin == "test_run" and test_identity.username == "E0071"


def test_a4_validate_uses_the_editor_identity_with_the_closure(projects):
    import inspect

    from app.core.projects import router as projects_router

    projects(e1=("estrai", _estrai_canvas(preview_username="E0071")))
    identity, closure = projects_router.editor_identity(_canvas_state(_reader("e1")), group_id=GROUP)
    assert identity.username == "E0071" and list(closure) == ["e1"]
    source = inspect.getsource(projects_router.validate_project_canvas)
    assert "editor_identity(selected_canvas, group_id=metadata.group_id)" in source
    assert "identity=preview_identity, closure=preview_closure" in source


LONG_LABEL = "Utente " + "x" * 300
LONG_KEY = "k" * 120


def test_a3_snapshot_is_validated_and_normalized(projects, recwarn):
    """A3: etichetta e chiave oltre i limiti: lo snapshot si costruisce col
    modello (nessun avviso di serializzazione), il manifest si rilegge e lo
    stesso input non risulta «tolto»."""
    from app.core.eseguibili.models import UPSTREAM_IDENTITY_LIMITS, ExecutableManifest, UpstreamIdentityInput

    projects(e1=("estrai", _estrai_canvas(label=LONG_LABEL, input_key=LONG_KEY)))
    closure = upstream_identity.identity_closure(_reader("e1"), group_id=GROUP)
    snapshot = upstream_plan.upstream_snapshot(closure)
    assert all(isinstance(row, UpstreamIdentityInput) for row in snapshot)
    assert len(snapshot[0].label) == UPSTREAM_IDENTITY_LIMITS["label"]
    assert len(snapshot[0].input_key) == UPSTREAM_IDENTITY_LIMITS["input_key"]
    manifest = ExecutableManifest(project_type="manipola", source_project_id="m").model_copy(
        update={"upstream_identity": snapshot})
    dumped = manifest.model_dump(mode="json")
    assert not [w for w in recwarn if "Serializ" in str(w.message)]
    assert ExecutableManifest.model_validate(dumped).upstream_identity == snapshot
    assert upstream_plan.removed_upstream_inputs(dumped["upstream_identity"], closure) == []
    # Uno snapshot salvato prima del limite (testi lunghi) si rilegge.
    raw = {**dumped, "upstream_identity": [{"project_id": "e1", "project_name": "P" * 400,
                                            "input_key": LONG_KEY, "label": LONG_LABEL}]}
    assert ExecutableManifest.model_validate(raw).upstream_identity[0].input_key == LONG_KEY[:80]
    assert upstream_plan.removed_upstream_inputs(raw["upstream_identity"], closure) == []
    # Tabella unica: ogni campo del modello con un limite e' nella tabella.
    limited = {name for name, field in UpstreamIdentityInput.model_fields.items()
               if any(getattr(m, "max_length", None) for m in field.metadata)}
    assert limited == set(UPSTREAM_IDENTITY_LIMITS)


def _legacy_version(canvas: dict) -> tuple[SimpleNamespace, SimpleNamespace]:
    version = SimpleNamespace(id="version-1", canvas=canvas, manifest={"project_type": "manipola"})
    manifest = SimpleNamespace(project_type="manipola", upstream_identity=[], inputs=[], bindings=[])
    return version, manifest


def test_a2_a_version_published_before_the_snapshot_gets_it_once(projects, monkeypatch):
    """A2 (scelta: backfill idempotente): la prima lettura scrive lo snapshot
    = chiusura attuale della versione; da li' togliere l'input blocca."""
    from app.core.eseguibili import db as eseguibili_db

    written: list = []
    monkeypatch.setattr(eseguibili_db, "backfill_version_upstream_identity",
                        lambda vid, rows: written.append((vid, rows)))
    projects(e1=("estrai", _estrai_canvas()))
    version, manifest = _legacy_version(_reader("e1"))
    assert upstream_plan.launch_removed_inputs(version=version, manifest=manifest, group_id=GROUP) == []
    assert written == [("version-1", [{"project_id": "e1", "project_name": "Progetto e1",
                                        "input_key": "utente", "label": "Utente"}])]
    # Lo snapshot ora c'e' (scritto): l'editor toglie l'input -> avvio fermo.
    version.manifest["upstream_identity"] = written[0][1]
    manifest.upstream_identity = upstream_plan.upstream_snapshot(
        upstream_identity.identity_closure(_reader("e1"), group_id=GROUP))
    projects(e1=("estrai", _plain_estrai()))
    with pytest.raises(RuntimeIdentityError, match="ripubblicarlo"):
        upstream_plan.plan_for_version(version=version, manifest=manifest, group_id=GROUP, raw_inputs={},
                                       identity=_admin("esecutore"))
    # Nessuna identita' a monte: nessuna scrittura (si ricalcola, costa poco).
    written.clear()
    version, manifest = _legacy_version(_reader("e1"))
    assert upstream_plan.version_snapshot(version, manifest, group_id=GROUP) == []
    assert written == []


def test_a2_backfill_sql_is_idempotent():
    from unittest.mock import patch

    from app.core.eseguibili import db as eseguibili_db

    with patch.object(eseguibili_db, "_execute") as execute:
        eseguibili_db.backfill_version_upstream_identity("v1", [])
    sql = execute.call_args.args[0]
    assert "(COALESCE(manifest, '{}'::jsonb) -> 'upstream_identity') IS NULL" in sql and "WHERE id = %s" in sql


def test_a6_detail_compares_the_published_closure_like_the_launch(projects, monkeypatch):
    """A6: N1 dice «avvio fermo» solo se l'avvio si ferma (chiusura della
    versione pubblicata); il canvas vivo che smette di leggere l'Estrai non
    e' un avvio fermo."""
    from app.core.eseguibili import service as eseguibili_service

    projects(e1=("estrai", _estrai_canvas()))
    snapshot = [row.model_dump() for row in upstream_plan.upstream_snapshot(
        upstream_identity.identity_closure(_reader("e1"), group_id=GROUP))]
    _executable, version = _eseguibile(_reader("e1"), snapshot=snapshot)
    assert eseguibili_service._launch_removed(version, GROUP) == []
    # Il progetto vivo non legge piu' e1 (canvas diverso): l'avvio della
    # versione pubblicata NON e' fermo; la ripubblicazione lo direbbe.
    rows = eseguibili_service._republish_preview_rows(version, _reader("e2"), SimpleNamespace(project_type="manipola"),
                                                      GROUP)
    assert [r["change"] for r in rows] == ["upstream_identity_removed"] and "nuova versione" in rows[0]["motivo"]
    # L'Estrai perde l'input: l'avvio e' fermo, N1 lo dice con la stessa funzione.
    projects(e1=("estrai", _plain_estrai()))
    launch = upstream_plan.upstream_identity_changes(eseguibili_service._launch_removed(version, GROUP), launch=True)
    assert [r["input_key"] for r in launch] == ["e1::utente"] and "l'avvio e' fermo" in launch[0]["motivo"]
    with pytest.raises(RuntimeIdentityError):
        upstream_plan.plan_for_version(version=version, manifest=eseguibili_service._tolerant_manifest(version.manifest),
                                       group_id=GROUP, raw_inputs={}, identity=_admin("esecutore"))


def test_a7_list_badge_uses_the_same_rule_as_the_detail(projects):
    from app.core.eseguibili import db as eseguibili_db
    from app.core.eseguibili import service as eseguibili_service

    projects(e1=("estrai", _estrai_canvas()))
    snapshot = [row.model_dump() for row in upstream_plan.upstream_snapshot(
        upstream_identity.identity_closure(_reader("e1"), group_id=GROUP))]
    _executable, version = _eseguibile(_reader("e1"), snapshot=snapshot)
    row = eseguibili_db.EseguibileSourceCanvasesRow(
        id="exe-1", version_canvas=_reader("e1"), project_canvas=_reader("e1"), version_id="version-1",
        version_manifest=version.manifest, project_type="manipola", group_id=GROUP)
    assert eseguibili_service._source_or_upstream_changed(row) is False
    projects(e1=("estrai", _plain_estrai()))
    assert eseguibili_service._source_or_upstream_changed(row) is True


ISTITUTO = "utente.str.slice(1, 4) + '_IS'"
ISTITUTO_LABEL = "Codice istituto, es. 035_IS (solo amministratori)"


def _archibus(n: int = 7) -> dict:
    """Il caso reale: n Estrai a monte con la stessa trasformazione e la
    stessa etichetta per gli amministratori."""
    return {f"e{i}": ("estrai", _estrai_canvas(identity_transform=ISTITUTO, admin_override=True,
                                               admin_override_label=ISTITUTO_LABEL)) for i in range(n)}


def test_b6_same_transform_and_label_is_one_field_for_every_upstream(projects):
    projects(**_archibus(), x=("estrai", _estrai_canvas(identity_transform=RULE, admin_override=True,
                                                         admin_override_label=ISTITUTO_LABEL)),
             y=("estrai", _estrai_canvas(identity_transform=ISTITUTO, admin_override=True,
                                         admin_override_label="Altra etichetta")))
    closure = upstream_identity.identity_closure(_reader(*[f"e{i}" for i in range(7)], "x", "y"), group_id=GROUP)
    groups = upstream_plan.identity_groups(closure)
    # Enumerazione: ogni input a monte e' in UN gruppo; gruppi diversi se la
    # trasformazione o l'etichetta differiscono.
    members = [m for g in groups for m in g["members"]]
    rows = upstream_identity.upstream_input_rows(closure)
    assert sorted(members) == sorted(r["key"] for r in rows) and len(members) == len(set(members))
    assert len(groups) == 3
    big = next(g for g in groups if len(g["members"]) == 7)
    assert big["key"].startswith(upstream_plan.GROUP_KEY_PREFIX)
    assert big["admin_override_label"] == ISTITUTO_LABEL and big["transform"] == ISTITUTO
    for row in rows:
        group = next(g for g in groups if row["key"] in g["members"])
        assert all(upstream_plan.group_signature(row) == upstream_plan.group_signature(other)
                   for other in rows if other["key"] in group["members"])
    # Chiave stabile: stessa firma -> stessa chiave, a ogni lettura.
    again = upstream_plan.identity_groups(upstream_identity.identity_closure(
        _reader(*[f"e{i}" for i in range(7)], "x", "y"), group_id=GROUP))
    assert [g["key"] for g in again] == [g["key"] for g in groups]


def test_b6_the_group_value_applies_to_every_member_with_may_choose_and_audit(projects):
    projects(**_archibus())
    canvas = _reader(*[f"e{i}" for i in range(7)])
    closure = upstream_identity.identity_closure(canvas, group_id=GROUP)
    [group] = upstream_plan.identity_groups(closure)
    own, plan = upstream_plan.plan_for_version(**_published(canvas, "manipola"), group_id=GROUP,
                                               raw_inputs={group["key"]: " 035_IS "}, identity=_admin("admin"))
    assert own == {}
    assert {_filter_value(plan.isolated[f"e{i}"]["canvas"])["value"] for i in range(7)} == {"035_IS"}
    audit = plan.summary[upstream_plan.GROUPS_SUMMARY_KEY][group["key"]]
    assert sorted(audit["inputs"]) == sorted(f"e{i}::utente" for i in range(7))
    assert all(plan.summary[f"e{i}"]["inputs"]["utente"]["overridden_by"] == "E0041" for i in range(7))
    # Una chiave singola resta valida e prevale per il suo input.
    _own, plan = upstream_plan.plan_for_version(
        **_published(canvas, "manipola"), group_id=GROUP,
        raw_inputs={group["key"]: "035_IS", "e0::utente": "001_IS"}, identity=_admin("admin"))
    assert _filter_value(plan.isolated["e0"]["canvas"])["value"] == "001_IS"
    assert _filter_value(plan.isolated["e1"]["canvas"])["value"] == "035_IS"
    # may_choose invariato per ogni input del gruppo: l'esecutore no.
    with pytest.raises(RuntimeIdentityError) as err:
        upstream_plan.plan_for_version(**_published(canvas, "manipola"), group_id=GROUP,
                                       raw_inputs={group["key"]: "035_IS"}, identity=_admin("esecutore"))
    assert err.value.code == CLIENT
    # Senza valore: ogni Estrai con lo username trasformato (come prima).
    _own, plan = upstream_plan.plan_for_version(**_published(canvas, "manipola"), group_id=GROUP, raw_inputs={},
                                                identity=_admin("admin", username="E0041"))
    assert {_filter_value(plan.isolated[f"e{i}"]["canvas"])["value"] for i in range(7)} == {"004_IS"}


def test_b6_an_unknown_group_key_is_refused(projects):
    projects(**_archibus(1))
    with pytest.raises(ValueError, match="non dichiarati"):
        upstream_plan.plan_for_version(**_published(_reader("e0"), "manipola"), group_id=GROUP,
                                       raw_inputs={upstream_plan.GROUP_KEY_PREFIX + "nessuno": "x"},
                                       identity=_admin("admin"))


def test_b6_the_own_input_with_the_same_signature_joins_the_group(projects):
    projects(**_archibus(2))
    own_input = SimpleNamespace(key="istituto", label="Utente", kind="current_user", transform=ISTITUTO,
                                admin_override=True, admin_override_label=ISTITUTO_LABEL)
    other = SimpleNamespace(key="altro", label="Altro", kind="current_user", transform=None,
                            admin_override=False, admin_override_label=None)
    closure = upstream_identity.identity_closure(_reader("e0", "e1"), group_id=GROUP)
    [group] = upstream_plan.identity_groups(closure, [own_input, other])
    assert sorted(group["members"]) == ["e0::utente", "e1::utente", "istituto"]
    expanded, audit = upstream_plan.expand_group_values({group["key"]: "035_IS"}, [group])
    assert expanded == {"e0::utente": "035_IS", "e1::utente": "035_IS", "istituto": "035_IS"}
    assert sorted(audit[group["key"]]["inputs"]) == sorted(group["members"])


def test_b6_detail_returns_one_row_per_group(projects):
    from app.core.eseguibili import service as eseguibili_service
    from app.core.eseguibili.models import UpstreamIdentityInputRow

    projects(**_archibus())
    executable, _version = _eseguibile(_reader(*[f"e{i}" for i in range(7)]))
    rows = eseguibili_service.upstream_identity_inputs(executable)
    assert len(rows) == 1 and len(rows[0]["members"]) == 7
    UpstreamIdentityInputRow.model_validate(rows[0])
