"""«Esegui di prova» dall'editor con «Utente che esegue» (regola H, 05/11/2026).

Regola (``app/shared/execution/runtime_identity``, sezione «Esecuzione di
prova»): la prova e' consentita SOLO a chi puo' modificare il progetto, usa
lo username di prova del canvas SALVATO, finisce nello spazio dell'attivita'
(mai nei risultati, nell'output_contract, nelle run intermedie del
progetto, mai Carica), ha una riga di cronologia marcata e non conta come
«ultima esecuzione». Schedule e catene restano bloccati.

Enumerazione:
 1. identita': factory ``RuntimeIdentity.test_run`` (stessa regola
    dell'anteprima, origine propria, motivo proprio senza username);
 2. percorsi HTTP x ruoli x casi (``PATHS``): editor -> consentita, contratto
    con soggetto ``test_run`` e username di prova; viewer/esecutore ->
    rifiutata; valore dal client -> rifiutato; senza username di prova ->
    bloccata; progetto senza input utente -> non applicabile; ogni chiamante
    di ``apply_test_run_inputs`` e' elencato;
 3. impronta: stima e avvio di prova coincidono; prova e avvio normale no;
 4. worker pipeline: prova isolata (spazio dell'attivita', nessuna run
    intermedia, nessun Carica, triggered_by marcato, output del progetto
    mai usato) e avvio normale invariato;
 5. worker Arricchisci: la run mode di prova (stesse domande);
 6. orchestrator: con il contratto di prova nessun output_contract e
    nessun Carica; senza identita' risolta (Schedule, catene) blocco;
 7. download: solo chi ha lanciato, dallo spazio dell'attivita';
 8. cronologia: ``latest-by-project`` esclude le prove.
"""

from __future__ import annotations

import copy
import inspect
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient

from app.core.artifacts.types import PipelineArtifact
from app.core.auth.dependencies import get_current_user
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.projects.canvas_models import CanvasState
from app.shared import error_codes
from app.shared.execution.runtime_bound_fields import runtime_static_parameters
from app.shared.execution.runtime_identity import (
    RuntimeIdentity,
    RuntimeIdentityError,
    resolve_identity_inputs,
    subject_for_test_run,
    trigger_for_test_run,
)
from tests2.helpers.builders import build_current_user

pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]

POS = {"x": 0, "y": 0}
REQUIRED = error_codes.RUNTIME_IDENTITY_REQUIRED
CLIENT = error_codes.RUNTIME_IDENTITY_CLIENT_VALUE
VALIDATION = error_codes.VALIDATION_ERROR
BACKEND = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- canvas


def _identity(target: str, field: str, **extra) -> dict:
    return {"id": "utente", "type": "project_input", "position": POS, "data": {
        "input_key": "utente", "label": "Utente", "input_kind": "current_user",
        "bindings": [{"target_node_id": target, "target_field": field}], **extra}}


def _estrai(**extra) -> dict:
    table = {"id": "t", "type": "source", "position": POS, "table_name": "pratiche",
             "columns": [{"name": "owner", "type": "str", "visible": True}],
             "filters": [{"column": "owner", "operator": "=", "value": ""}]}
    return {"nodes": [table, {"id": "out", "type": "result", "position": POS},
                      _identity("t", "filters.0.value", **extra)],
            "edges": [{"id": "o1", "source": "t", "target": "out", "join_type": "output"},
                      {"id": "b", "source": "utente", "target": "t"}]}


def _manipola(**extra) -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": []},
        {"id": "f", "type": "transform", "position": POS, "transform_op": "filter",
         "config": {"column": "owner", "operator": "==", "value": ""}},
        {"id": "out", "type": "result", "position": POS},
        _identity("f", "config.value", **extra),
    ], "edges": [{"id": "e1", "source": "src", "target": "f"}, {"id": "e2", "source": "f", "target": "out"},
                 {"id": "b", "source": "utente", "target": "f"}]}


def _arricchisci(**extra) -> dict:
    return {"nodes": [
        {"id": "src", "type": "staticValues", "position": POS, "fields": []},
        {"id": "api", "type": "api_enrich", "position": POS, "api_id": "api-1",
         "static_parameters": {"utente": ""}},
        {"id": "out", "type": "result", "position": POS},
        _identity("api", "static_parameters.utente", **extra),
    ], "edges": [{"id": "e1", "source": "src", "target": "api"}, {"id": "e2", "source": "api", "target": "out"},
                 {"id": "b", "source": "utente", "target": "api"}]}


CANVASES = {"estrai": _estrai, "manipola": _manipola, "arricchisci": _arricchisci}


def _without_identity(project_type: str) -> dict:
    canvas = CANVASES[project_type]()
    canvas["nodes"] = [n for n in canvas["nodes"] if n["id"] != "utente"]
    canvas["edges"] = [e for e in canvas["edges"] if e["source"] != "utente"]
    return canvas


def _bound_value(canvas: dict, project_type: str):
    nodes = {n["id"]: n for n in canvas["nodes"]}
    if project_type == "estrai":
        return nodes["t"]["filters"][0]["value"]
    if project_type == "manipola":
        return nodes["f"]["config"]["value"]
    return runtime_static_parameters(nodes["api"])["utente"]


# ---------------------------------------------------------------- 1. identita'


def test_factory_reads_the_saved_test_username_with_its_own_origin():
    identity = RuntimeIdentity.test_run(_manipola(preview_username=" prova "))
    assert (identity.origin, identity.username) == ("test_run", "prova")
    assert RuntimeIdentity.preview_of(_manipola(preview_username="prova")).origin == "preview"
    assert subject_for_test_run(identity) == {"kind": "test_run", "username": "prova"}
    with pytest.raises(ValueError):
        subject_for_test_run(RuntimeIdentity.preview_of(_manipola(preview_username="prova")))


def test_factory_without_test_username_blocks_and_says_what_to_do():
    identity = RuntimeIdentity.test_run(_manipola())
    assert (identity.username, identity.reason) == (None, "test_run_missing")
    defs = [SimpleNamespace(key="utente", label="Utente", kind="current_user")]
    with pytest.raises(RuntimeIdentityError) as err:
        resolve_identity_inputs(defs, identity=identity, client_values={})
    assert err.value.code == REQUIRED and "esecuzione di prova" in err.value.message


def test_factory_with_two_different_test_usernames_is_a_conflict():
    canvas = _manipola(preview_username="a")
    other = copy.deepcopy(canvas["nodes"][-1])
    other["id"], other["data"]["preview_username"] = "utente2", "b"
    canvas["nodes"].append(other)
    assert RuntimeIdentity.test_run(canvas).reason == "preview_conflict"


def test_project_run_message_points_to_the_test_run():
    defs = [SimpleNamespace(key="utente", label="Utente", kind="current_user")]
    with pytest.raises(RuntimeIdentityError) as err:
        resolve_identity_inputs(defs, identity=RuntimeIdentity.project_run(), client_values={})
    assert "Esegui di prova" in err.value.message


def test_contract_rejects_a_test_subject_without_username():
    with pytest.raises(ValueError):
        PipelineExecutionContract(project_id="p", project_type="manipola", group_id="g", canvas={},
                                  execution_subject={"kind": "test_run", "username": " "})
    contract = PipelineExecutionContract(project_id="p", project_type="manipola", group_id="g", canvas={},
                                         execution_subject={"kind": "test_run", "username": "prova"})
    assert contract.isolated_output and contract.executable_subject is None
    assert contract.build_runtime_context(output_task_id="t")["persist_contract"] is False


# ---------------------------------------------------------------- 2. percorsi


ROLES_ALLOWED = ("user", "admin", "superadmin")
ROLES_DENIED = ("viewer", "esecutore")


def _app(project_type: str, canvas: dict, monkeypatch, role: str = "user"):
    from app.core.arricchisci import router as enrichment
    from app.core.orchestrator import router as pipeline
    from app.core.orchestrator.task_service import task_service

    metadata = SimpleNamespace(project_type=project_type, group_id="grp-1", canvas_validated=True,
                               default_connection_id="conn")
    service = SimpleNamespace(
        get_project_metadata=lambda _: metadata,
        get_project_canvas=lambda _: CanvasState(**copy.deepcopy(canvas)),
        get_project_output_contract=lambda _: None,
        get_canvas_version=lambda _: "v1",
    )
    submitted = MagicMock(return_value=SimpleNamespace(task_id="task-1", status=SimpleNamespace(value="pending")))
    owners = MagicMock()
    monkeypatch.setattr(enrichment, "_project_service", service)
    monkeypatch.setattr(enrichment, "_prepare_canvas_for_owner", lambda project_id, c, *a, **k: c)
    monkeypatch.setattr(enrichment, "_api_connection_service", object())
    monkeypatch.setattr(enrichment, "_check_project_ownership", lambda *_: metadata)
    monkeypatch.setattr(enrichment, "assert_group_access", lambda *a, **k: None)
    monkeypatch.setattr(enrichment, "_preflight_connections", lambda *_: {})
    monkeypatch.setattr(enrichment, "estimate_canvas", lambda *_: [])
    monkeypatch.setattr(enrichment, "register_test_run_owner", owners)
    monkeypatch.setattr(pipeline, "register_test_run_owner", owners)
    monkeypatch.setattr(pipeline, "_orchestrator", SimpleNamespace(project_service=service))
    monkeypatch.setattr(pipeline, "_assert_project_group", lambda *_: None)
    monkeypatch.setattr(pipeline, "_task_info_to_dict", lambda _: {"task_id": "task-1"})
    monkeypatch.setattr(task_service, "reserve_task_id", lambda: "task-1")
    for name in ("create_pipeline_task", "create_enrichment_task"):
        monkeypatch.setattr(task_service, name, submitted)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user(username="chi.lancia", role=role)
    app.include_router(enrichment.enrich_router, prefix="/api")
    app.include_router(pipeline.pipeline_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False), submitted, owners


def _code(response) -> str:
    body = response.json()
    detail = body.get("detail", body)
    return detail.get("error") if isinstance(detail, dict) else str(detail)


def _launch_url(project_type: str) -> str:
    return "/api/projects/p/test-run/enrich" if project_type == "arricchisci" else "/api/projects/p/test-run"


def _launch(project_type: str):
    def run(case, role, monkeypatch):
        canvas = (_without_identity(project_type) if case == "no_identity_input"
                  else CANVASES[project_type](**({} if case == "no_test_username" else {"preview_username": "prova"})))
        client, submitted, owners = _app(project_type, canvas, monkeypatch, role=role)
        inputs = {"utente": "anna.bianchi"} if case == "client_value" else {}
        response = client.post(_launch_url(project_type), json={"inputs": inputs})
        if response.status_code != 200:
            assert not submitted.called and not owners.called
            return "refused", response.status_code, _code(response)
        contract = submitted.call_args.kwargs["execution_contract"]
        assert submitted.call_args.kwargs["task_id"] == "task-1"
        owners.assert_called_once_with("task-1", project_id="p", user_id="u-1", group_id="grp-1")
        assert response.json()["test_run"] == {"username": "prova"}
        assert contract["execution_subject"] == {"kind": "test_run", "username": "prova"}
        assert all(n.get("type") != "project_input" for n in contract["canvas"]["nodes"])
        assert "intermediate_mode_override" not in submitted.call_args.kwargs or \
            submitted.call_args.kwargs["intermediate_mode_override"] is None
        return "ok", 200, _bound_value(contract["canvas"], project_type)
    return run


def _preflight(case, role, monkeypatch):
    canvas = (_without_identity("arricchisci") if case == "no_identity_input"
              else _arricchisci(**({} if case == "no_test_username" else {"preview_username": "prova"})))
    client, _, _ = _app("arricchisci", canvas, monkeypatch, role=role)
    inputs = {"utente": "anna.bianchi"} if case == "client_value" else {}
    response = client.post("/api/projects/p/test-run/preflight", json={"inputs": inputs})
    if response.status_code != 200:
        return "refused", response.status_code, _code(response)
    return "ok", 200, response.json()["test_run"]["username"]


OK = ("ok", 200, "prova")
CASES = {
    "editor": OK,
    "client_value": ("refused", 400, CLIENT),
    "no_test_username": ("refused", 400, REQUIRED),
    "no_identity_input": ("refused", 400, VALIDATION),
}
PATHS = {
    "prova_estrai": _launch("estrai"),
    "prova_manipola": _launch("manipola"),
    "prova_arricchisci": _launch("arricchisci"),
    "stima_prova_arricchisci": _preflight,
}
MATRIX = [(path, case, role) for path in PATHS for case in CASES for role in ("user",)] + \
    [(path, "editor", role) for path in PATHS for role in ROLES_ALLOWED + ROLES_DENIED if role != "user"]


@pytest.mark.parametrize("path,case,role", MATRIX, ids=[f"{p}-{c}-{r}" for p, c, r in MATRIX])
def test_every_test_run_path(path, case, role, monkeypatch):
    outcome = PATHS[path](case, role, monkeypatch)
    if role in ROLES_DENIED:
        assert outcome[:2] == ("refused", 403)
    elif path == "stima_prova_arricchisci" and case == "no_identity_input":
        # La stima passa dal mappatore unico degli errori di preparazione.
        assert outcome[0] == "refused" and outcome[1] in (400, 422)
    else:
        assert outcome == CASES[case]


def test_preflight_never_takes_a_client_canvas(monkeypatch):
    client, _, _ = _app("arricchisci", _arricchisci(preview_username="prova"), monkeypatch)
    response = client.post("/api/projects/p/test-run/preflight",
                           json={"inputs": {}, "canvas": _arricchisci(preview_username="anna.bianchi")})
    assert response.status_code == 422  # campo non ammesso nella richiesta


def test_launch_never_takes_a_client_canvas_or_intermediate_mode(monkeypatch):
    for project_type in ("manipola", "arricchisci"):
        client, submitted, _ = _app(project_type, CANVASES[project_type](preview_username="prova"), monkeypatch)
        response = client.post(_launch_url(project_type),
                               json={"inputs": {}, "canvas": CANVASES[project_type](preview_username="x")})
        assert response.status_code == 422 and not submitted.called
        response = client.post(_launch_url(project_type) + "?intermediate_mode=full_artifact", json={"inputs": {}})
        assert response.status_code == 200
        assert submitted.call_args.kwargs.get("intermediate_mode_override") is None


def test_the_saved_canvas_decides_the_username(monkeypatch):
    """Il canvas salvato ha «prova»: nessun corpo della richiesta lo cambia."""
    client, submitted, _ = _app("manipola", _manipola(preview_username="prova"), monkeypatch)
    response = client.post("/api/projects/p/test-run", json={"inputs": {}, "preview_username": "anna.bianchi"})
    assert response.status_code == 422 and not submitted.called


def test_project_execute_stays_blocked(monkeypatch):
    client, submitted, _ = _app("manipola", _manipola(preview_username="prova"), monkeypatch)
    response = client.post("/api/projects/p/execute", json={"inputs": {}})
    assert response.status_code == 400 and _code(response) == REQUIRED and not submitted.called


def test_arricchisci_uses_its_own_test_run(monkeypatch):
    client, submitted, _ = _app("arricchisci", _arricchisci(preview_username="prova"), monkeypatch)
    response = client.post("/api/projects/p/test-run", json={"inputs": {}})
    assert response.status_code == 400 and not submitted.called


def test_every_caller_of_the_test_run_is_enumerated():
    """Chi costruisce un contratto di prova: le due prove e la stima, e
    nessun altro (Schedule, catene, Carica, Eseguibili non ci passano)."""
    callers = {}
    for path in (BACKEND / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        count = len(re.findall(r"apply_test_run_inputs\(", text)) - len(re.findall(r"def apply_test_run_inputs\(", text))
        if count:
            callers[path.relative_to(BACKEND).as_posix()] = count
    assert callers == {
        "app/core/orchestrator/router.py": 1,
        "app/core/arricchisci/router.py": 1,
        "app/core/arricchisci/canvas_preflight.py": 1,
    }
    for path in (BACKEND / "app" / "core" / "scheduler").rglob("*.py"):
        assert not re.search(r"\btest_run\b|test-run|TEST_RUN", path.read_text(encoding="utf-8")), path


# ---------------------------------------------------------------- 3. impronta


def test_preflight_and_launch_share_the_fingerprint(monkeypatch):
    from app.core.arricchisci.canvas_preflight import preparation_fingerprint, prepare_contract

    client, submitted, _ = _app("arricchisci", _arricchisci(preview_username="prova"), monkeypatch)
    estimate = client.post("/api/projects/p/test-run/preflight", json={"inputs": {}}).json()
    response = client.post("/api/projects/p/test-run/enrich",
                           json={"inputs": {}, "preparation_fingerprint": estimate["preparation_fingerprint"]})
    assert response.status_code == 200
    stale = client.post("/api/projects/p/test-run/enrich", json={"inputs": {}, "preparation_fingerprint": "x"})
    assert stale.status_code == 409 and "preparation_changed" in stale.text
    # La stessa preparazione senza soggetto di prova ha un'altra impronta.
    base = PipelineExecutionContract(project_id="p", project_type="arricchisci", group_id="grp-1",
                                     canvas=CanvasState(**_arricchisci(preview_username="prova")).model_dump())
    contract, _ = prepare_contract(base, {}, test_run=True)
    plain = contract.model_copy(update={"execution_subject": None})
    assert preparation_fingerprint(contract, {}, revision="v1") == estimate["preparation_fingerprint"]
    assert preparation_fingerprint(plain, {}, revision="v1") != estimate["preparation_fingerprint"]


# ---------------------------------------------------------------- 4. worker pipeline


class _Recorder:
    def __init__(self, artifact):
        self.artifact = artifact
        self.calls = []

    def execute_pipeline_from_contract(self, **kwargs):
        self.calls.append(kwargs)
        return self.artifact


def _run_worker(monkeypatch, contract, artifact):
    from workers.tasks import pipeline_task as pt

    recorder = _Recorder(artifact)
    log = MagicMock()
    lifecycle = MagicMock()
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: recorder)
    monkeypatch.setattr(pt, "_update_progress", lambda *a, **k: None)
    monkeypatch.setattr(pt, "_close_worker_connection", lambda **k: None)
    monkeypatch.setattr(pt.async_user_recheck_module, "recheck_user_authorized", lambda **k: None)
    monkeypatch.setattr(pt, "open_run", log.open_run)
    monkeypatch.setattr(pt, "close_run", log.close_run)
    monkeypatch.setattr(pt, "set_run_context", lambda **k: None)
    monkeypatch.setattr(pt, "get_steps", lambda: [])
    monkeypatch.setattr(pt.run_lifecycle_module, "create_run", lifecycle.create_run)
    monkeypatch.setattr(pt.run_lifecycle_module, "close_run", lifecycle.close_run)
    monkeypatch.setattr(pt, "_resolve_intermediate_retention_n", lambda _: None)
    monkeypatch.setattr(pt.debug_payload_builder_module, "DebugPayloadBuilder", MagicMock())
    shim = SimpleNamespace(request=SimpleNamespace(id="task-1"), name="pipeline.execute")
    result = pt.execute_pipeline_task(shim, project_id="p", user_id="u-1",
                                      execution_contract=contract.to_payload())
    return result, recorder, log, lifecycle


def _contract(subject=None):
    return PipelineExecutionContract(project_id="p", project_type="manipola", group_id="grp-1",
                                     canvas={"nodes": [], "edges": []}, execution_subject=subject)


TEST_SUBJECT = {"kind": "test_run", "username": "prova"}


def test_worker_test_run_is_isolated(monkeypatch):
    artifact = PipelineArtifact(project_id="p", success=True, storage_key="exec/task-1/o.csv",
                                output_path="o.csv", rows_count=3, result_id=None)
    result, recorder, log, lifecycle = _run_worker(monkeypatch, _contract(TEST_SUBJECT), artifact)
    call = recorder.calls[0]
    assert (call["skip_carica"], call["output_task_id"], call["run_id"]) == (True, "task-1", None)
    assert log.open_run.call_args.kwargs["triggered_by"] == trigger_for_test_run("prova") == "test_run:prova"
    assert not lifecycle.create_run.called and not lifecycle.close_run.called
    assert log.close_run.call_args.kwargs["status"] == "success"
    assert result["success"] is True and result["result_id"] is None
    assert result["test_run"] == {"username": "prova"} and result["output_file"] == "o.csv"
    assert "is_eseguibile" not in result and "download_url" not in result


def test_worker_test_run_that_wrote_the_project_result_fails(monkeypatch):
    artifact = PipelineArtifact(project_id="p", success=True, storage_key="results/x", result_id="r-1",
                                output_path="o.csv")
    with pytest.raises(RuntimeError, match="output isolato"):
        _run_worker(monkeypatch, _contract(TEST_SUBJECT), artifact)


def test_worker_test_run_failure_keeps_the_mark(monkeypatch):
    artifact = PipelineArtifact(project_id="p", success=False, message="rotto")
    result, _, log, lifecycle = _run_worker(monkeypatch, _contract(TEST_SUBJECT), artifact)
    assert result["success"] is False and result["test_run"] == {"username": "prova"}
    assert log.close_run.call_args.kwargs["status"] == "failure" and not lifecycle.close_run.called


def test_worker_normal_project_run_is_unchanged(monkeypatch):
    artifact = PipelineArtifact(project_id="p", success=True, result_id="r-1", output_path="o.csv")
    result, recorder, log, lifecycle = _run_worker(monkeypatch, _contract(), artifact)
    call = recorder.calls[0]
    assert (call["skip_carica"], call["output_task_id"]) == (False, None)
    assert call["run_id"] == result["run_id"]
    assert log.open_run.call_args.kwargs["triggered_by"] == "manual"
    assert lifecycle.create_run.called and lifecycle.close_run.called
    assert result["result_id"] == "r-1" and "test_run" not in result


# ---------------------------------------------------------------- 5. worker Arricchisci


def test_enrichment_test_run_mode_is_isolated(monkeypatch):
    from app.core.arricchisci import enrichment_run_mode as modes

    mode = modes.for_task(False, {"username": "prova"})
    assert isinstance(mode, modes.ProjectTestRunMode)
    assert (mode.isolated_output, mode.is_eseguibile, mode.logs_run(), mode.should_recheck_auth()) == \
        (True, False, True, True)
    assert not mode.triggers_downstream_carica() and not mode.triggers_carica_after_dataframe()
    assert mode.triggered_by() == "test_run:prova" and mode.needs_full_bytes()
    created = MagicMock()
    monkeypatch.setattr(modes.run_lifecycle_module, "create_run", created)
    ctx = mode.begin_run(project_id="p", user_id="u-1", task_id="task-1", canonical_group_id="grp-1")
    assert (ctx.run_id, ctx.group_id) == (None, "grp-1") and not created.called
    persisted = MagicMock(return_value=SimpleNamespace(storage_key="k", output_filename="o.csv"))
    monkeypatch.setattr(modes.ExecutableOutputPersistence, "persist_dataframe_output", persisted)
    monkeypatch.setattr(modes.pipeline_result_module, "save_pipeline_result_outcome", MagicMock(side_effect=AssertionError))
    monkeypatch.setattr(modes.output_contract_module, "persist_output_contract", MagicMock(side_effect=AssertionError))
    result_id, path = mode.persist_dataframe_result(
        project_id="p", task_id="task-1", final_df=None, final_format="csv", output_filename="o",
        final_filename="o.csv", group_id="grp-1")
    assert result_id is None and path.name == "o.csv" and persisted.called
    result = {"result_id": "x"}
    mode.decorate_result(result, project_id="p", task_id="task-1")
    assert result == {"result_id": None, "test_run": {"username": "prova"}}


def test_other_enrichment_modes_are_unchanged():
    from app.core.arricchisci import enrichment_run_mode as modes

    standard, executable = modes.for_task(False), modes.for_task(True)
    assert (standard.isolated_output, standard.triggers_downstream_carica(),
            standard.triggers_carica_after_dataframe(), standard.triggered_by()) == (False, True, True, "manual")
    assert (executable.isolated_output, executable.triggers_downstream_carica(),
            executable.triggers_carica_after_dataframe(), executable.logs_run()) == (True, False, True, False)


def test_enrichment_worker_reads_isolation_from_the_run_mode():
    source = (BACKEND / "workers" / "tasks" / "enrichment_task.py").read_text(encoding="utf-8")
    assert "run_mode.is_eseguibile" not in source
    assert "triggered_by=run_mode.triggered_by()" in source
    assert source.count("triggers_carica_after_dataframe()") == 1
    assert "for_task(is_eseguibile, test_run)" in source
    assert "output_task_id=task_id if parsed_contract.isolated_output else None" in source


# ---------------------------------------------------------------- 6. orchestrator


class _Executor:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append(kwargs)
        return {"success": True, "storage_key": "k", "output_path": "o.csv"}

    def preview(self, *args, **kwargs):
        self.calls.append(kwargs)
        return {"success": True}


def _orchestrator(executor):
    from app.core.orchestrator.service import OrchestratorService
    return OrchestratorService(project_service=MagicMock(), executor=MagicMock(), generator=MagicMock(),
                               connection_service=MagicMock(), estrai_executor=executor,
                               manipola_executor=executor, enrich_executor=executor)


def test_orchestrator_with_the_test_contract_skips_contract_and_carica(monkeypatch):
    executor = _Executor()
    orchestrator = _orchestrator(executor)
    monkeypatch.setattr(orchestrator, "_check_pipeline_memory", lambda: None)
    carica = MagicMock()
    monkeypatch.setattr(orchestrator, "_execute_carica_if_present", carica)
    canvas = {"nodes": [{"id": "out", "type": "result", "position": POS}], "edges": []}
    contract = _contract(TEST_SUBJECT).model_copy(update={"canvas": canvas})
    artifact = orchestrator.execute_pipeline_from_contract(
        contract=contract, skip_carica=True, output_task_id="task-1")
    assert artifact.success and not carica.called
    context = executor.calls[0]["execution_context"]
    assert context["persist_contract"] is False and context["output_task_id"] == "task-1"
    assert executor.calls[0]["output_persistence"].task_id == "task-1"


def test_schedule_and_chains_stay_blocked(monkeypatch):
    """Schedule, catene, Carica: il canvas arriva con il nodo non risolto."""
    executor = _Executor()
    orchestrator = _orchestrator(executor)
    monkeypatch.setattr(orchestrator, "_check_pipeline_memory", lambda: None)
    contract = _contract().model_copy(update={"canvas": _manipola(preview_username="prova")})
    artifact = orchestrator.execute_pipeline_from_contract(contract=contract)
    assert not artifact.success and artifact["error"] == REQUIRED and not executor.calls


# ---------------------------------------------------------------- 7. download


def _download(monkeypatch, *, owner_user: str, caller: str):
    from app.core.orchestrator import access_control
    from app.core.orchestrator import router as pipeline

    task = SimpleNamespace(status=SimpleNamespace(value="success"), project_id="p",
                           result={"project_id": "p", "result_id": None, "output_file": "o.csv",
                                   "test_run": {"username": "prova"}})
    metadata = SimpleNamespace(group_id="grp-1")
    monkeypatch.setattr(pipeline, "_assert_task_group", lambda *_: task)
    monkeypatch.setattr(pipeline, "_orchestrator", SimpleNamespace(
        project_service=SimpleNamespace(get_project_metadata=lambda _: metadata)))
    monkeypatch.setattr(pipeline, "_task_eseguibile_record", lambda *_: None)
    monkeypatch.setattr(access_control.eseguibili_output_store, "load_task_owner",
                        lambda task_id, group_id: {"eseguibile_id": "p", "user_id": owner_user})
    monkeypatch.setattr(pipeline._eseg_output_store, "find_output_keys", lambda *a, **k: ["exec/task-1/o.csv"])
    stream = MagicMock(return_value=Response(content=b"x"))
    monkeypatch.setattr(pipeline, "build_consume_once_response", stream)
    blob = MagicMock(side_effect=AssertionError("mai i risultati del progetto"))
    monkeypatch.setattr(pipeline.results_blob, "open_result_stream", blob)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user(user_id=caller, role="user")
    app.include_router(pipeline.tasks_router, prefix="/api")
    response = TestClient(app, raise_server_exceptions=False).get("/api/tasks/task-1/download")
    return response, stream, blob


def test_download_of_a_test_run_is_only_for_who_launched_it(monkeypatch):
    response, stream, blob = _download(monkeypatch, owner_user="u-1", caller="u-1")
    assert response.status_code == 200 and stream.call_args.kwargs["group_id"] == "grp-1"
    assert not blob.called
    response, stream, blob = _download(monkeypatch, owner_user="u-1", caller="u-2")
    assert response.status_code == 403 and not stream.called and not blob.called


# ---------------------------------------------------------------- 8. cronologia


def test_latest_run_by_project_ignores_test_runs(monkeypatch):
    from app.core.logs import runs_router

    seen = {}

    def fetchall(sql, params):
        seen["sql"], seen["params"] = sql, params
        return []

    monkeypatch.setattr(runs_router, "_fetchall", fetchall)
    runs_router.get_latest_run_by_project(current_user=build_current_user(role="user"))
    assert "NOT LIKE %s" in seen["sql"] and seen["params"][0] == "test\\_run:%"
    assert trigger_for_test_run("mario.rossi").startswith("test_run:")


def test_docstring_declares_the_rule():
    from app.shared.execution import runtime_identity

    doc = inspect.getdoc(runtime_identity)
    for words in ("ESECUZIONE DI PROVA", "canvas SALVATO", "nessun Carica", "test_run:<username>",
                  "Schedule e catene"):
        assert words in doc
