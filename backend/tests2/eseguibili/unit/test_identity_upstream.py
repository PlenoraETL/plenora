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
        canvas=_reader("e1"), manifest=SimpleNamespace(project_type="manipola", upstream_identity=[]),
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
    assert snapshot == [{"project_id": "e1", "project_name": "Progetto e1", "input_key": "utente", "label": "Utente"}]
    upstream_plan.assert_upstream_snapshot(snapshot, closure)  # invariato: si avvia
    projects(e1=("estrai", _plain_estrai()))
    gone = upstream_identity.identity_closure(_reader("e1"), group_id=GROUP)
    with pytest.raises(RuntimeIdentityError, match="ripubblicarlo"):
        upstream_plan.assert_upstream_snapshot(snapshot, gone)
    rows = upstream_plan.upstream_identity_changes({"upstream_identity": snapshot}, gone)
    assert [(r["change"], r["blocking"]) for r in rows] == [("upstream_identity_removed", True)]
    # Un input a monte AGGIUNTO dopo la pubblicazione restringe: nessuna riga.
    assert upstream_plan.upstream_identity_changes({"upstream_identity": []}, closure) == []


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
# Chiamate che leggono o ricalcolano un progetto (risultato salvato, ricalcolo
# a monte, ricalcolo isolato).
READ_CALLS = {
    "LatestProjectArtifactRef", "latest_project_artifact_ref_cls", "materialize_latest", "pipeline_dispatcher",
    "_recompute_upstream_project", "isolated_dispatcher", "execute_isolated_upstream", "load_in_memory",
    "resolve_project_file_bytes",
}
GATED = "porta"  # la funzione chiama route_upstream_source prima di leggere
# Ogni sito: "porta" oppure il motivo per cui non e' una lettura a monte o
# la funzione che ha gia' chiamato la porta. Un sito nuovo fa fallire il test.
UPSTREAM_READ_SITES = {
    "core/manipola/project_source_runtime.py:load_project_sources": GATED,
    "core/manipola/project_source_runtime.py:_materialize_latest_project_source": (
        "chiamata solo da load_project_sources, dopo la porta"),
    "core/manipola/project_source_runtime.py:_load_isolated_upstream": "ramo «isolated» della porta",
    "core/arricchisci/api_connection_source_loading.py:_load_source_project": GATED,
    "core/arricchisci/api_connection_rest_file.py:_resolve_file_source_path": GATED,
    "core/arricchisci/source_request_loader.py:load_request_data": GATED,
    "core/manipola/canvas_data_preparation.py:_output": GATED,
    "core/artifacts/source_loader.py:_load_project": "infrastruttura del caricatore (chi la chiama passa la porta)",
    "core/artifacts/source_loader.py:load_in_memory": "infrastruttura del caricatore",
    "core/artifacts/source_loader.py:resolve_project_file_bytes": "infrastruttura del caricatore",
    "core/artifacts/types.py:from_node_data": "costruzione del riferimento, nessuna lettura",
    "core/carica/dispatch.py:maybe_multilayer_arcgis_write": "Carica legge il PROPRIO risultato appena salvato",
    "core/carica/conflict_request_loader.py:_load_project_conflict_dataframe": GATED,
    "core/features/routes.py:_resolve_input_dataframe": "pubblicazione del PROPRIO risultato",
    "runtime/services.py:dispatch_isolated_upstream": "cablaggio del ramo «isolated»",
}


def _sites() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        rel = path.relative_to(APP).as_posix()

        def visit(node, func=None):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = node
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else None
                hit = name in READ_CALLS
                if name == "from_node_data" and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "SourceRef":
                    first = node.args[0] if node.args else None
                    hit = not (isinstance(first, ast.Constant) and first.value != "project_source")
                if hit:
                    key = f"{rel}:{func.name if func else '<module>'}"
                    found[key] = ast.get_source_segment(source, func) if func else source
            for child in ast.iter_child_nodes(node):
                visit(child, func)

        visit(tree)
    return found


def test_every_upstream_read_passes_through_the_gate():
    sites = _sites()
    assert sorted(sites) == sorted(UPSTREAM_READ_SITES), (
        "Nuova lettura di un progetto (risultato salvato o ricalcolo): passa dalla porta "
        "runtime_identity.route_upstream_source e aggiungila a UPSTREAM_READ_SITES")
    for key, rule in UPSTREAM_READ_SITES.items():
        if rule == GATED:
            assert "route_upstream_source(" in sites[key], key


def test_every_gate_call_reads_the_closure():
    """La porta riceve sempre il bisogno letto dalla chiusura viva."""
    for path in APP.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if "route_upstream_source(" in source and "def route_upstream_source" not in source:
            assert source.count("route_upstream_source(") == source.count("need=source_identity_need("), path


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
            canvas=_reader("e1"), manifest=SimpleNamespace(project_type="arricchisci", upstream_identity=[]),
            group_id=GROUP, raw_inputs={}, identity=_admin("esecutore"))


def test_preflight_fingerprint_follows_the_upstream(projects):
    user = build_current_user(username="E0041", role="esecutore", group_id=GROUP)
    projects(e1=("estrai", _estrai_canvas()))
    _s, first, _ = _endpoint(user, {}, path="preflight")
    projects(e1=("estrai", _estrai_canvas(identity_transform=RULE)))
    _s, second, _ = _endpoint(user, {}, path="preflight")
    assert first["preparation_fingerprint"] != second["preparation_fingerprint"]
