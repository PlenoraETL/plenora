"""«Esegui di prova»: la REGOLA MADRE delle scritture (correzione dopo la
conferma di G+H, 05/11/2026).

Regola (``app/shared/execution/runtime_identity``, sezione «Esecuzione di
prova»): la prova non scrive NULLA fuori dallo spazio della propria attivita'
e dalla riga ``log_runs`` marcata. Ogni scrittura esistente dei worker e'
enumerata qui con la ragione per cui in prova non avviene (o resta nello
spazio dell'attivita').

Enumerazione:
 1. ricalcolo a monte: Manipola e Arricchisci (sorgente «progetto") x
    {artefatto mancante, scaduto, ricalcolo forzato} -> la prova si ferma con
    «Esegui prima il progetto a monte», nessun dispatcher, nessuna run;
    l'esecuzione normale ricalcola ancora; gli endpoint di prova rifiutano
    ``force_recalculate``; il contratto porta il segno agli executor;
 2. ``triggered_by`` dal client solo da ``CLIENT_TRIGGERS``; il prefisso
    ``test_run:`` solo dalla factory del server; un Carica non e' una prova;
 3. ruolo ricontrollato al prelievo (pipeline e Arricchisci) e al download;
 4. lo schema della connessione API non si aggiorna in prova;
 5. ogni ramo di esito del worker pipeline porta il segno della prova;
 6. tabella delle scritture dei worker = scritture presenti nel sorgente.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.artifacts.types import PipelineArtifact
from app.core.auth.dependencies import get_current_user
from app.shared.contracts.auth import ROLES_ABOVE_VIEWER, VALID_ROLES
from app.shared.execution.runtime_identity import (
    CLIENT_TRIGGERS,
    TEST_RUN_NO_RECALCULATE_MESSAGE,
    TEST_RUN_ROLE_REVOKED_MESSAGE,
    TestRunUpstreamRefused,
    can_test_run,
    guard_upstream_recompute,
    trigger_for_test_run,
)
from tests2.helpers.builders import build_current_user
from tests2.orchestrator.unit import test_project_test_run as base

pytestmark = [pytest.mark.unit, pytest.mark.orchestrator, pytest.mark.contract]

BACKEND = Path(__file__).resolve().parents[3]
MARK = {"test_run": {"username": "prova"}}
UPSTREAM_MESSAGE = "Esegui prima il progetto a monte «Anagrafe»"


# ---------------------------------------------------------------- 1. a monte


UPSTREAM_CASES = ("missing", "stale", "forced")


def _freshness(case: str):
    return SimpleNamespace(stale=case == "stale", source_project_name="Anagrafe",
                           message="Il progetto sorgente e' stato modificato (o rilancia con ricalcolo forzato)")


def _manipola_load(monkeypatch, case: str, *, test_run: bool):
    from app.core.manipola import project_source_runtime as psr

    writes = MagicMock()
    monkeypatch.setattr(psr, "get_project_source_freshness", lambda *a, **k: _freshness(case))
    monkeypatch.setattr(psr, "_materialize_latest_project_source", lambda *a, **k: None)
    monkeypatch.setattr(psr, "_open_upstream_run", lambda *a, **k: (writes.open_run(), None))
    monkeypatch.setattr(psr, "_close_upstream_run", lambda *a, **k: writes.close_run())
    monkeypatch.setattr(psr, "cleanup_worker_memory", lambda: {})
    dispatcher = MagicMock(return_value=SimpleNamespace(success=False, message="giu'", rows_count=0,
                                                        output_path=None))
    canvas = SimpleNamespace(nodes=[SimpleNamespace(id="n", type="project_source",
                                                    data={"source_project_id": "up"})])
    result = psr.load_project_sources(canvas, {}, force_recalculate=case == "forced",
                                      pipeline_dispatcher=dispatcher, group_id="grp-1", test_run=test_run)
    return result, dispatcher, writes


@pytest.mark.parametrize("case", UPSTREAM_CASES)
def test_manipola_test_run_never_recomputes_upstream(case, monkeypatch):
    result, dispatcher, writes = _manipola_load(monkeypatch, case, test_run=True)
    assert result["success"] is False and UPSTREAM_MESSAGE in result["message"]
    assert "ricalcolo forzato" not in result["message"]
    assert not dispatcher.called and not writes.open_run.called and not writes.close_run.called


@pytest.mark.parametrize("case", ("missing", "forced"))
def test_manipola_normal_run_still_recomputes_upstream(case, monkeypatch):
    _, dispatcher, writes = _manipola_load(monkeypatch, case, test_run=False)
    assert dispatcher.called and writes.open_run.called
    assert dispatcher.call_args.kwargs["force_recalculate"] is True


def test_manipola_normal_run_stale_keeps_its_message(monkeypatch):
    result, dispatcher, _ = _manipola_load(monkeypatch, "stale", test_run=False)
    assert "ricalcolo forzato" in result["message"] and not dispatcher.called


def _arricchisci_load(monkeypatch, case: str, *, test_run: bool):
    from app.core.arricchisci import api_connection_source_loading as sl
    from app.core.arricchisci.execution_context import ArricchisciExecutionContext

    stack = MagicMock()
    monkeypatch.setattr(sl, "get_project_source_freshness", lambda *a, **k: _freshness(case))
    monkeypatch.setattr(sl.SourceLoader, "load", lambda *a, **k: None)
    monkeypatch.setattr("app.runtime.services.build_orchestrator_stack", stack)
    svc = sl.ApiConnectionSourceLoadingMixin()
    svc._upstream_blob_is_stale = lambda *a, **k: case == "stale"
    svc._assert_source_project_in_tenant = lambda *a, **k: None
    ctx = ArricchisciExecutionContext(project_id="p", group_id="grp-1", preview_limit=None, test_run=test_run)
    with pytest.raises(ValueError) as err:
        svc._load_source_project({"source_project_id": "up"}, force_recalculate=case == "forced", ctx=ctx)
    return err.value, stack


@pytest.mark.parametrize("case", UPSTREAM_CASES)
def test_arricchisci_test_run_never_recomputes_upstream(case, monkeypatch):
    error, stack = _arricchisci_load(monkeypatch, case, test_run=True)
    assert isinstance(error, TestRunUpstreamRefused) and not stack.called
    if case == "stale":
        assert UPSTREAM_MESSAGE in str(error)


def test_arricchisci_normal_run_still_recomputes_upstream(monkeypatch):
    _, stack = _arricchisci_load(monkeypatch, "missing", test_run=False)
    assert stack.called  # poi fallisce perche' il finto stack non produce output


def test_the_guard_is_the_only_door_and_knows_the_subject():
    guard_upstream_recompute(test_run=False, source_name="x")
    with pytest.raises(TestRunUpstreamRefused, match="Esegui prima il progetto a monte «x»"):
        guard_upstream_recompute(test_run=True, source_name="x")
    callers = {}
    for path in (BACKEND / "app").rglob("*.py"):
        count = len(re.findall(r"guard_upstream_recompute\(", path.read_text(encoding="utf-8")))
        if count and path.name != "runtime_identity.py":
            callers[path.relative_to(BACKEND).as_posix()] = count
    assert callers == {"app/core/manipola/project_source_runtime.py": 1,
                       "app/core/arricchisci/api_connection_source_loading.py": 1}
    # Nessun altro ricalcolo a monte: chi richiama la pipeline di un altro progetto.
    upstream = [p.relative_to(BACKEND).as_posix() for p in (BACKEND / "app").rglob("*.py")
                if re.search(r"\.execute_pipeline\(|\bpipeline_dispatcher\(", p.read_text(encoding="utf-8"))]
    assert sorted(upstream) == ["app/core/arricchisci/api_connection_source_loading.py",
                                "app/core/manipola/project_source_runtime.py", "app/runtime/services.py"]


def test_the_contract_tells_the_executors():
    test = base._contract(base.TEST_SUBJECT).build_runtime_context(output_task_id="t")
    assert test["test_run"] is True
    assert "test_run" not in base._contract().build_runtime_context()
    executable = base._contract({"kind": "eseguibile", "id": "e"}).build_runtime_context(output_task_id="t")
    assert "test_run" not in executable


def test_executors_pass_the_mark_to_the_upstream_loaders():
    manipola = (BACKEND / "app/core/manipola/executor.py").read_text(encoding="utf-8")
    assert 'test_run=bool((execution_context or {}).get("test_run"))' in manipola
    arricchisci = (BACKEND / "app/core/arricchisci/executor.py").read_text(encoding="utf-8")
    assert 'test_run=bool(execution_context.get("test_run"))' in arricchisci
    worker = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    assert "test_run=test_run is not None,\n        )" in worker


@pytest.mark.parametrize("url", ["/api/projects/p/test-run", "/api/projects/p/test-run/enrich",
                                 "/api/projects/p/test-run/preflight"])
def test_test_run_endpoints_refuse_recalculate(url, monkeypatch):
    project_type = "manipola" if url.endswith("/test-run") else "arricchisci"
    client, submitted, owners = base._app(project_type, base.CANVASES[project_type](preview_username="prova"),
                                          monkeypatch)
    response = client.post(url, json={"inputs": {}, "force_recalculate": True})
    assert response.status_code == 400 and base._code(response) == base.VALIDATION
    assert TEST_RUN_NO_RECALCULATE_MESSAGE in response.text
    assert not submitted.called and not owners.called


def test_worker_ignores_recalculate_in_a_test_run(monkeypatch):
    from workers.tasks import pipeline_task as pt

    seen = {}
    artifact = PipelineArtifact(project_id="p", success=True, storage_key="exec/task-1/o.csv",
                                output_path="o.csv", result_id=None)
    recorder = base._Recorder(artifact)
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: recorder)
    for name in ("_update_progress", "_close_worker_connection", "set_run_context", "open_run", "close_run"):
        monkeypatch.setattr(pt, name, lambda *a, **k: None)
    monkeypatch.setattr(pt, "get_steps", lambda: [])
    monkeypatch.setattr(pt.async_user_recheck_module, "recheck_user_authorized",
                        lambda **k: seen.update(k))
    shim = SimpleNamespace(request=SimpleNamespace(id="task-1"), name="pipeline.execute")
    pt.execute_pipeline_task(shim, project_id="p", user_id="u-1", force_recalculate=True,
                             execution_contract=base._contract(base.TEST_SUBJECT).to_payload())
    assert recorder.calls[0]["force_recalculate"] is False
    assert seen["test_run"] is True  # punto 3: anche il ruolo


# ---------------------------------------------------------------- 2. triggered_by


def test_client_trigger_only_from_the_declared_list():
    from app.core.orchestrator.router import CreateTaskRequest

    payload = {"task_type": "carica", "project_id": "p", "canvas": {}}
    for value in CLIENT_TRIGGERS:
        assert CreateTaskRequest(**payload, triggered_by=value).triggered_by == value
    for value in ("test_run:x", "scheduler", "cron", "cascade", "pipeline", "eseguibile", ""):
        with pytest.raises(ValidationError):
            CreateTaskRequest(**payload, triggered_by=value)


def test_carica_is_never_a_test_run():
    from app.core.orchestrator.execution_contract import CaricaExecutionContract

    kwargs = dict(project_id="p", project_type="manipola", group_id="g", canvas={}, carica_node_data={})
    assert CaricaExecutionContract(**kwargs, triggered_by="scheduler").triggered_by == "scheduler"
    with pytest.raises(ValidationError):
        CaricaExecutionContract(**kwargs, triggered_by=trigger_for_test_run("x"))
    source = (BACKEND / "workers/tasks/carica_task.py").read_text(encoding="utf-8")
    assert "if is_test_run_trigger(triggered_by):" in source


def test_only_the_server_factory_writes_the_test_run_prefix():
    offenders = []
    for root in ("app", "workers"):
        for path in (BACKEND / root).rglob("*.py"):
            if path.name == "runtime_identity.py":
                continue
            if re.search(r"""["']test_run:""", path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(BACKEND).as_posix())
    assert offenders == []


# ---------------------------------------------------------------- 3. ruolo


def test_role_rule_is_the_one_of_require_user_or_above():
    assert {r for r in VALID_ROLES if can_test_run(r)} == set(ROLES_ABOVE_VIEWER)
    assert not can_test_run("viewer") and not can_test_run("esecutore")


def _recheck(monkeypatch, role: str, *, test_run: bool):
    from app.core.auth import async_user_recheck as recheck

    monkeypatch.setattr(recheck, "get_user", lambda _: SimpleNamespace(
        id="u-1", username="anna", group_id="grp-1", role=role, is_active=True))
    monkeypatch.setattr(recheck, "assert_project_access", lambda *a, **k: None)
    return recheck.recheck_user_authorized("u-1", "p", task_id="t", test_run=test_run)


@pytest.mark.parametrize("role", VALID_ROLES)
def test_worker_recheck_of_a_test_run_includes_the_role(role, monkeypatch):
    outcome = _recheck(monkeypatch, role, test_run=True)
    if role in ROLES_ABOVE_VIEWER:
        assert outcome is None
    else:
        assert outcome["status"] == "permission_revoked"
        assert outcome["message"] == TEST_RUN_ROLE_REVOKED_MESSAGE
    # Fuori dalla prova il ruolo non cambia il ricontrollo (invariato).
    assert _recheck(monkeypatch, role, test_run=False) is None


def test_enrichment_worker_rechecks_the_role_of_a_test_run():
    source = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    block = source[source.index('task_name="ENRICHMENT"'):][:200]
    assert "test_run=test_run is not None" in block


def _download(monkeypatch, role: str):
    from app.core.orchestrator import access_control
    from app.core.orchestrator import router as pipeline

    task = SimpleNamespace(status=SimpleNamespace(value="success"), project_id="p",
                           result={"project_id": "p", "result_id": None, "output_file": "o.csv", **MARK})
    monkeypatch.setattr(pipeline, "_assert_task_group", lambda *_: task)
    monkeypatch.setattr(pipeline, "_orchestrator", SimpleNamespace(
        project_service=SimpleNamespace(get_project_metadata=lambda _: SimpleNamespace(group_id="grp-1"))))
    monkeypatch.setattr(pipeline, "_task_eseguibile_record", lambda *_: None)
    monkeypatch.setattr(access_control.eseguibili_output_store, "load_task_owner",
                        lambda task_id, group_id: {"eseguibile_id": "p", "user_id": "u-1"})
    monkeypatch.setattr(pipeline._eseg_output_store, "find_output_keys", lambda *a, **k: ["exec/task-1/o.csv"])
    stream = MagicMock(return_value=Response(content=b"x"))
    monkeypatch.setattr(pipeline, "build_consume_once_response", stream)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user(user_id="u-1", role=role)
    app.include_router(pipeline.tasks_router, prefix="/api")
    response = TestClient(app, raise_server_exceptions=False).get("/api/tasks/task-1/download")
    return response, stream


@pytest.mark.parametrize("role", VALID_ROLES)
def test_download_of_a_test_run_rechecks_the_role(role, monkeypatch):
    response, stream = _download(monkeypatch, role)
    if role in ROLES_ABOVE_VIEWER:
        assert response.status_code == 200 and stream.called
    else:
        assert response.status_code == 403 and not stream.called


# ---------------------------------------------------------------- 4. schema API


def test_test_run_never_updates_the_api_connection_schema():
    source = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    assert source.count("update_api_connection(") == 1
    guard = source[:source.index("update_api_connection(")].rsplit("if (", 1)[1]
    assert guard.lstrip().startswith("test_run is None")


# ---------------------------------------------------------------- 5. esiti del worker


def _worker(monkeypatch, *, artifact=None, revoked=None, idempotent=False, run_id=None):
    from workers.tasks import pipeline_task as pt

    recorder = base._Recorder(artifact)
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: recorder)
    for name in ("_update_progress", "_close_worker_connection", "set_run_context", "open_run", "close_run"):
        monkeypatch.setattr(pt, name, lambda *a, **k: None)
    monkeypatch.setattr(pt, "get_steps", lambda: [])
    monkeypatch.setattr(pt.debug_payload_builder_module, "DebugPayloadBuilder", MagicMock())
    monkeypatch.setattr(pt.async_user_recheck_module, "recheck_user_authorized",
                        lambda **k: dict(revoked) if revoked else None)
    monkeypatch.setattr(pt.run_idempotency_module, "is_already_completed", lambda _: idempotent)
    shim = SimpleNamespace(request=SimpleNamespace(id="task-1"), name="pipeline.execute")
    return pt.execute_pipeline_task(shim, project_id="p", user_id="u-1", run_id=run_id,
                                    execution_contract=base._contract(base.TEST_SUBJECT).to_payload())


OUTCOMES = {
    "permesso_revocato": dict(revoked={"status": "permission_revoked", "success": False,
                                       "message": TEST_RUN_ROLE_REVOKED_MESSAGE}),
    "idempotenza": dict(idempotent=True, run_id="r-1"),
    "connessione_persa": dict(artifact=PipelineArtifact(project_id="p", success=False,
                                                        message="server closed the connection unexpectedly")),
    "fallimento": dict(artifact=PipelineArtifact(project_id="p", success=False, message="rotto")),
    "successo": dict(artifact=PipelineArtifact(project_id="p", success=True, storage_key="exec/task-1/o.csv",
                                               output_path="o.csv", result_id=None)),
}


@pytest.mark.parametrize("branch", OUTCOMES)
def test_every_worker_outcome_carries_the_test_mark(branch, monkeypatch):
    result = _worker(monkeypatch, **OUTCOMES[branch])
    assert result["test_run"] == MARK["test_run"]
    if branch == "connessione_persa":
        assert result.get("retryable") is True
    if branch == "permesso_revocato":
        assert result["status"] == "permission_revoked" and result["message"] == TEST_RUN_ROLE_REVOKED_MESSAGE


def _task_returns_and_raises(func) -> tuple[list[str], list[str]]:
    """``return``/``raise`` del corpo del task, senza le funzioni annidate
    che non restituiscono l'esito del task (helper interni)."""
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    task = tree.body[0]
    returns: list[str] = []
    raises: list[str] = []

    def visit(node, inside_outcome: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                if getattr(child, "name", "") == "_outcome":
                    visit(child, True)
                continue  # helper annidati: non sono esiti del task
            if isinstance(child, ast.Return) and not inside_outcome:
                returns.append(ast.unparse(child.value) if child.value is not None else "None")
            if isinstance(child, ast.Raise):
                raises.append(ast.unparse(child.exc) if child.exc is not None else "raise")
            visit(child, inside_outcome)

    visit(task, False)
    return returns, raises


def _worker_funcs():
    from workers.tasks import enrichment_task, pipeline_task

    return {"pipeline": pipeline_task.execute_pipeline_task, "arricchisci": enrichment_task.execute_enrichment_task}


# Numero di esiti dichiarati per worker (return): se ne aggiungi uno, passa da
# _outcome e aggiorna qui.
WORKER_RETURNS = {"pipeline": 6, "arricchisci": 8}


@pytest.mark.parametrize("worker", sorted(WORKER_RETURNS))
def test_every_return_of_the_worker_goes_through_the_outcome(worker):
    """REGOLA: ogni ``return`` del task passa da ``_outcome``, cioe' da
    ``runtime_identity.task_result`` (l'unica funzione dei risultati)."""
    import inspect

    func = _worker_funcs()[worker]
    returns, _ = _task_returns_and_raises(func)
    assert all(r.startswith("_outcome(") for r in returns), returns
    assert len(returns) == WORKER_RETURNS[worker], returns
    assert "return task_result(payload, test_run)" in inspect.getsource(func)


@pytest.mark.parametrize("worker", sorted(WORKER_RETURNS))
def test_every_raise_of_the_worker_is_finalized_with_the_mark(worker):
    """Un esito in eccezione (``raise``) non passa da ``_outcome``: lo scrive
    il middleware del worker con ``finalize_from_celery``, che riceve il
    soggetto di prova dal contratto del messaggio e usa ``task_result``."""
    _, raises = _task_returns_and_raises(_worker_funcs()[worker])
    assert raises, "nessun raise enumerato"
    middleware = (BACKEND / "workers/dramatiq_app.py").read_text(encoding="utf-8")
    assert "contract_test_run_subject(" in middleware and "test_run=test_run," in middleware
    assert "task_result({" in middleware  # anche il RETRY


@pytest.mark.parametrize("final", ["exception", "revoked", "returned", "returned_non_dict"])
def test_finalization_of_any_outcome_carries_the_mark(final):
    from types import SimpleNamespace as NS

    from app.core.orchestrator import task_progress as tp
    from app.shared.execution.runtime_identity import contract_test_run_subject

    written = []
    store = NS(put=lambda task_id, state, payload, name=None: written.append((state, payload)))
    subject = contract_test_run_subject(base._contract(base.TEST_SUBJECT).to_payload())
    assert subject == MARK["test_run"]
    einfo = {"exception": NS(exception=RuntimeError("boom")), "revoked": NS(exception=tp.TaskRevoked("t"))}.get(final)
    status = "FAILURE" if einfo else "SUCCESS"
    retval = {"returned": {"success": False, "message": "x"}, "returned_non_dict": "pong"}.get(final)
    tp.finalize_from_celery("t", status, retval, einfo, name="pipeline.execute", store=store, test_run=subject)
    ((state, payload),) = written
    assert payload["test_run"] == MARK["test_run"], (state, payload)


def test_finalization_without_a_test_run_is_unchanged():
    from types import SimpleNamespace as NS

    from app.core.orchestrator import task_progress as tp
    from app.shared.execution.runtime_identity import contract_test_run_subject

    written = []
    store = NS(put=lambda task_id, state, payload, name=None: written.append(payload))
    assert contract_test_run_subject(base._contract().to_payload()) is None
    assert contract_test_run_subject(None) is None and contract_test_run_subject({"execution_subject": "x"}) is None
    tp.finalize_from_celery("t", "FAILURE", None, NS(exception=RuntimeError("boom")), store=store)
    assert "test_run" not in written[0]


# ---------------------------------------------------------------- 6. tabella delle scritture


WRITE_CALL = re.compile(
    r"\b((?:\w+\.)?(?:save|persist|update|record|create|close|open|complete|cancel|write|insert|delete)_\w*)\(")
NOT_WRITES = {"update_task_progress", "_update_progress", "_close_worker_connection", "open_result_stream",
              "eseg_storage.open_task_stream", "close_worker_connection",
              "run_lifecycle_module.close_worker_connection"}

# (file, chiamata) -> perche' in prova non scrive fuori dall'attivita'.
WRITES_IN_TEST_RUN = {
    ("pipeline_task.py", "open_run"): "riga log_runs marcata test_run:<username> (ammessa)",
    ("pipeline_task.py", "close_run"): "chiude la riga log_runs marcata (ammessa)",
    ("pipeline_task.py", "run_lifecycle_module.create_run"): "solo con intermediate_run (falso in prova)",
    ("pipeline_task.py", "run_lifecycle_module.close_run"): "solo con intermediate_run (falso in prova)",
    ("pipeline_task.py", "eseguibili_service.complete_eseguibile_run"): "solo Eseguibili (subject)",
    ("pipeline_task.py", "eseguibili_service.cancel_eseguibile_run"): "solo Eseguibili (subject)",
    ("pipeline_task.py", "eseguibili_storage.save_chunks"): "spazio dell'attivita' (task_id)",
    ("pipeline_task.py", "file_source_service.delete_file_source"): "input temporanei dell'Eseguibile",
    ("pipeline_task.py", "persist_preview_payload"): "solo anteprima (altro task)",
    ("enrichment_task.py", "open_run"): "riga log_runs marcata (run_mode.triggered_by)",
    ("enrichment_task.py", "close_run"): "chiude la riga log_runs marcata",
    ("enrichment_task.py", "intermediate_writer.close_run"): "solo con _run_id (None in prova)",
    ("enrichment_task.py", "intermediate_snapshot.record_checkpoint_snapshot"): "solo con _run_id (None in prova)",
    ("enrichment_task.py", "intermediate_snapshot.record_final_snapshot"): "solo con _run_id (None in prova)",
    ("enrichment_task.py", "api_connection_service.update_api_connection"): "mai in prova (test_run is None)",
    ("enrichment_task.py", "result_blob_writer.save_result_blob_from_bytes"): "solo senza isolated_output",
    ("enrichment_task.py", "eseg_storage.save_bytes"): "spazio dell'attivita' (task_id)",
    ("enrichment_task.py", "run_mode.persist_dataframe_result"): "ProjectTestRunMode: spazio dell'attivita'",
    ("enrichment_task.py", "complete_executable_run"): "solo Eseguibili (executable_run_id)",
    ("enrichment_task.py", "cancel_executable_run"): "solo Eseguibili (executable_run_id)",
    ("enrichment_task.py", "eseguibili_service.complete_eseguibile_run"): "solo Eseguibili",
    ("enrichment_task.py", "eseguibili_service.cancel_eseguibile_run"): "solo Eseguibili",
    ("enrichment_task.py", "file_source_service.delete_file_source"): "input temporanei dell'attivita'",
}


def test_every_worker_write_is_enumerated():
    found = set()
    for name in ("pipeline_task.py", "enrichment_task.py"):
        source = (BACKEND / "workers" / "tasks" / name).read_text(encoding="utf-8")
        source = re.sub(r"^\s*def \w+\(", "", source, flags=re.M)
        for call in WRITE_CALL.findall(source):
            if call not in NOT_WRITES:
                found.add((name, call))
    assert found == set(WRITES_IN_TEST_RUN)


def test_enrichment_isolation_guards_are_in_place():
    source = (BACKEND / "workers/tasks/enrichment_task.py").read_text(encoding="utf-8")
    for call in ("intermediate_writer.close_run(", "intermediate_snapshot.record_final_snapshot("):
        for match in re.finditer(re.escape(call), source):
            assert "_run_id" in source[max(0, match.start() - 200):match.start()], call
    for match in re.finditer(r"result_blob_writer\.save_result_blob_from_bytes\(", source):
        window = source[max(0, match.start() - 2500):match.start()]
        assert "run_mode.isolated_output" in window
