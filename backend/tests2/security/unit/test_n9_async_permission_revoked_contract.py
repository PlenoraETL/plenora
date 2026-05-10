"""
Contract test N9 â€” re-check permessi user in pipeline_task.

Previous behavior scenario:
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from types import SimpleNamespace
from typing import Optional
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


OTHER_USER_TASKS = [
    ("preview_pipeline_task", "workers.tasks.pipeline_task"),
    ("execute_carica_task", "workers.tasks.carica_task"),
    ("execute_enrichment_task", "workers.tasks.enrichment_task"),
]

OTHER_USER_SERVICES = [
    (
        "create_preview_task",
        "preview_pipeline_task",
        "workers.tasks.pipeline_task",
        {"project_id": "p1", "limit": 10},
    ),
    (
        "create_carica_task",
        "execute_carica_task",
        "workers.tasks.carica_task",
        {"project_id": "p1", "canvas": {"nodes": [], "edges": []}, "carica_node_data": {}},
    ),
    (
        "create_enrichment_task",
        "execute_enrichment_task",
        "workers.tasks.enrichment_task",
        {"project_id": "p1", "canvas": {"nodes": [], "edges": []}, "output_format": "csv"},
    ),
]


# Helpers â€” bytecode probe (refactor-resilient)

def _underlying(callable_or_task):
    """Estrae la funzione Python sottostante da un task Celery wrappato.

    Celery `@app.task` wrappa il callable in un Task subclass; il body
    originale e' accessibile via `__wrapped__` quando bind=True/False.
    Se non c'e' wrapping, ritorna l'oggetto stesso (es. fn pura).
    """
    return getattr(callable_or_task, "__wrapped__", callable_or_task)


def _references_name(callable_or_task, name: str) -> bool:
    """True se il bytecode di `callable` referenzia `name` come callee/attr.

    Verifica sia `co_names` (top-level) sia ricorsivamente i nested code
    objects in `co_consts` (closures, comprehensions). Pattern preso da
    PR #37 (`test_enrichment_task_tenant_binding_lock.py`): refactor-
    resilient ai rename di variabili locali, alle riformattazioni e
    all'aggiunta di commenti, ma sensibile al rename del callee â€” che
    e' esattamente l'invariant da blindare.
    """
    code = _underlying(callable_or_task).__code__

    def _walk(co) -> bool:
        if name in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    return _walk(code)


def _assert_optional_user_id(callable_obj, missing_message: str):
    sig = inspect.signature(callable_obj)
    assert "user_id" in sig.parameters, missing_message
    assert sig.parameters["user_id"].default is None
    return sig


def _capture_execute(monkeypatch, target):
    captured_calls = []

    def spy_execute(sql, params=()):
        captured_calls.append({"sql": sql, "params": tuple(params)})

    monkeypatch.setattr(target, spy_execute)
    return captured_calls


def _assert_single_insert_user_id(captured_calls, table_name, expected_user_id, label):
    assert len(captured_calls) == 1, (
        f"{label} deve eseguire 1 INSERT. Captured: {captured_calls}"
    )
    sql = captured_calls[0]["sql"]
    params = captured_calls[0]["params"]

    assert f"INSERT INTO {table_name}" in sql, (
        f"SQL deve essere INSERT INTO {table_name}"
    )
    assert "user_id" in sql, (
        f"INSERT {table_name} deve includere la colonna user_id."
    )
    assert expected_user_id in params, (
        f"user_id non bindato nei params (hardcoded NULL?). Params: {params}"
    )


def _assert_user_id_migration_exists(filename_part, alter_table, missing_message):
    mig_dir = Path(__file__).resolve().parents[3] / "migrations"
    candidates = [f for f in mig_dir.iterdir() if filename_part in f.name]
    assert len(candidates) >= 1, missing_message.format(mig_dir=mig_dir)
    sql = candidates[0].read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS user_id" in sql
    assert alter_table in sql


def _task_service_signature_target(service_method):
    from app.core.orchestrator import task_service as task_service_mod
    from app.core.orchestrator.task_service import TaskService

    if hasattr(TaskService, service_method):
        return getattr(TaskService, service_method)
    return getattr(task_service_mod, f"_{service_method}_compat")


def _patch_pipeline_eager_success(monkeypatch):
    from workers.tasks import pipeline_task as pt
    from workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)
    monkeypatch.setattr(pt.execute_pipeline_task, "update_state", lambda *a, **kw: None)
    monkeypatch.setattr(
        "app.core.intermediate.run_idempotency.is_already_completed",
        lambda run_id: False,
    )
    monkeypatch.setattr(
        "app.core.auth.async_user_recheck.recheck_user_authorized",
        lambda **kwargs: None,
    )

    fake_orchestrator = MagicMock()
    fake_result = MagicMock(success=True, message="ok")
    fake_result.to_dict = lambda: {"success": True}
    fake_orchestrator.execute_pipeline.return_value = fake_result
    monkeypatch.setattr(pt, "_build_orchestrator", lambda: fake_orchestrator)
    return pt


# 1. Task accetta user_id


def test_execute_pipeline_task_accepts_user_id_kwarg():
    """execute_pipeline_task deve accettare `user_id` optional per
    re-check permessi al pickup del worker."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    _assert_optional_user_id(execute_pipeline_task, (
        "execute_pipeline_task non accetta user_id. Senza, il re-check "
        "permessi al pickup non e' possibile."
    ))


def test_execute_pipeline_task_references_recheck_helper():
    """Il task deve referenziare `recheck_user_authorized` come callee
    (single source of truth helper). Bytecode probe: refactor-resilient
    a rinames locali, sensibile a rename del callee (cosa voluta).

    Replacement R-04 di `inspect.getsource(...) + "recheck_user_authorized" in src`.
    """
    from workers.tasks.pipeline_task import execute_pipeline_task

    assert _references_name(execute_pipeline_task, "recheck_user_authorized"), (
        "execute_pipeline_task non riferisce recheck_user_authorized. "
        "Rischio drift della logica re-check tra i worker."
    )


# Test droppato (R-04, 2026-05-01):
#   `test_execute_pipeline_task_emits_permission_revoked_status` (pre-PR)
#   verificava `inspect.getsource(recheck_user_authorized)` contiene la
#   stringa "permission_revoked". Ridondante con
#   `test_shared_recheck_returns_permission_revoked_on_disabled_user`
#   (sotto) che asserisce `result["status"] == "permission_revoked"`
#   come vero behavior osservabile. Coverage dello status string e'
#   garantita dal behavior test.


# 2. Service e router forward user_id


def test_create_pipeline_task_accepts_user_id():
    """task_service.create_pipeline_task deve accettare e forward user_id."""
    from app.core.orchestrator.task_service import TaskService

    _assert_optional_user_id(TaskService.create_pipeline_task, (
        "TaskService.create_pipeline_task non accetta user_id. "
        "Senza, il router non puo' propagare l'identita' alla task."
    ))


def test_create_pipeline_task_forwards_user_id_to_delay(monkeypatch):
    """`create_pipeline_task` deve invocare `execute_pipeline_task.delay`
    propagando user_id come kwarg.

    Replacement R-04 di `inspect.getsource(...) + "user_id" in src`:
    spy su `.delay` -> osservazione diretta del kwarg arrivato.
    """
    from workers.tasks import pipeline_task as pt
    from app.core.orchestrator.task_service import task_service

    captured = {}

    class _FakeAsyncResult:
        id = "celery-task-1"

    def fake_delay(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncResult()

    monkeypatch.setattr(pt.execute_pipeline_task, "delay", fake_delay)

    info = task_service.create_pipeline_task(
        project_id="proj_1",
        force_recalculate=False,
        user_id="usr_x",
    )

    assert info.task_id == "celery-task-1"
    assert captured.get("user_id") == "usr_x", (
        f"create_pipeline_task non forward user_id a .delay. Captured: {captured}"
    )
    assert captured.get("project_id") == "proj_1"


def test_execute_endpoint_passes_current_user_id(monkeypatch):
    """Il router POST /projects/{id}/execute deve propagare
    `current_user.user_id` al service.

    Replacement R-04 di `inspect.getsource(execute_project_pipeline)`:
    TestClient + dependency override + spy su create_pipeline_task.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.core.auth.auth import require_not_esecutore
    from app.core.orchestrator.router import pipeline_router, init_orchestrator
    from tests2.helpers.builders import build_current_user

    init_orchestrator(MagicMock())

    fixture_user = build_current_user(user_id="usr_endpoint", role="admin")
    captured = {}

    def spy_create_pipeline_task(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            task_id="t-1",
            to_dict=lambda: {"task_id": "t-1", "status": "pending"},
        )

    monkeypatch.setattr(
        "app.core.orchestrator.router._assert_project_group",
        lambda *a, **kw: None,
    )
    monkeypatch.setattr(
        "app.core.orchestrator.task_service.task_service.create_pipeline_task",
        spy_create_pipeline_task,
    )

    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: fixture_user
    app.include_router(pipeline_router, prefix="/api")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.post("/api/projects/proj_1/execute")
    assert response.status_code == 200
    assert captured.get("user_id") == "usr_endpoint", (
        "execute_project_pipeline non forward current_user.user_id al service. "
        "La task partira' senza identita' -> re-check no-op. "
        f"Captured: {captured}"
    )


# 3. Hardening (refinement N9): assert_project_access + user_id persistito


def test_shared_recheck_invokes_assert_project_access(monkeypatch):
    """Il re-check deve chiamare `assert_project_access` (stesso helper
    HTTP) per coerenza tra HTTP gate e worker gate.

    Replacement R-04 di `inspect.getsource(recheck_user_authorized) + "assert_project_access" in src`:
    spy diretto sul collaborator -> osserva la chiamata al helper.
    """
    from app.core.auth import async_user_recheck as mod
    from app.core.auth.db import UserRow

    monkeypatch.setattr(
        mod,
        "get_user",
        lambda uid: UserRow(
            id=uid,
            username="u",
            group_id="g_1",
            role="admin",
            is_active=True,
        ),
    )

    spy_calls = []

    def spy_apa(project_id, current_user):
        spy_calls.append({
            "project_id": project_id,
            "user_id": current_user.user_id,
            "group_id": current_user.group_id,
        })

    monkeypatch.setattr(mod, "assert_project_access", spy_apa)

    result = mod.recheck_user_authorized(user_id="usr_x", project_id="proj_1")

    assert result is None, "User attivo + access OK -> task procede (None)"
    assert len(spy_calls) == 1, "assert_project_access deve essere chiamato"
    assert spy_calls[0]["project_id"] == "proj_1"
    assert spy_calls[0]["user_id"] == "usr_x"


def test_pipeline_task_passes_user_id_to_open_run(monkeypatch):
    """Il task deve propagare user_id a `open_run` per audit
    (log_runs.user_id).

    Replacement R-04 di `inspect.getsource(...) + "user_id=user_id" in src`:
    eager mode + spy su `open_run`. Mockiamo i collaborator pesanti
    (idempotency, recheck, orchestrator, close_run) per ridurre il
    body del task al pass-through che ci interessa.
    """
    pt = _patch_pipeline_eager_success(monkeypatch)

    # Spy su open_run: NB il task fa `from app.utils.logger import open_run`
    # at module load -> il symbol e' bound in `workers.tasks.pipeline_task`,
    # non riferito a ogni call all'origine. Pattern simmetrico al monkeypatch
    # di `get_user` in `test_shared_recheck_returns_permission_revoked_on_disabled_user`.
    captured_open_run = {}

    def spy_open_run(**kwargs):
        captured_open_run.update(kwargs)

    monkeypatch.setattr("workers.tasks.pipeline_task.open_run", spy_open_run)

    # Spy su close_run: no-op silente (anche questo bound in pt namespace).
    monkeypatch.setattr(
        "workers.tasks.pipeline_task.close_run", lambda **kwargs: None,
    )

    # Spy su intermediate run lifecycle create_run: no-op (covered separately).
    monkeypatch.setattr(
        "app.core.intermediate.run_lifecycle.create_run",
        lambda *args, **kwargs: {"run_id": kwargs.get("run_id", "r-1")},
    )

    # Invoca il task in eager mode con user_id concreto.
    pt.execute_pipeline_task.apply(
        kwargs={
            "project_id": "proj_1",
            "user_id": "usr_x",
            "run_id": "r-1",
        },
    )

    assert captured_open_run.get("user_id") == "usr_x", (
        "Il task non passa user_id a open_run. L'audit trail in log_runs "
        "non avra' l'identita' dell'utente che ha lanciato la run. "
        f"Captured: {captured_open_run}"
    )


def test_open_run_accepts_user_id_kwarg():
    """La funzione `open_run` (wrapper modulo) deve accettare user_id
    optional per persistenza audit."""
    from app.utils.logger import open_run

    _assert_optional_user_id(open_run, (
        "open_run non accetta user_id. Senza, non possiamo persistere "
        "chi ha lanciato la run nel log_runs."
    ))


def test_open_run_writes_user_id_in_insert_sql(monkeypatch):
    """L'INSERT su log_runs deve includere la colonna user_id e il valore
    deve apparire nei params bindati (non hardcoded NULL).

    Replacement R-04 di `inspect.getsource(PostgresLogHandler.open_run) + "user_id" in src`:
    SQL capture via spy su pg_connection.execute -> boundary DB
    osservabile. Robusto a rename interni / riformattazione SQL.
    """
    from app.utils import logger as logger_mod

    captured_calls = _capture_execute(monkeypatch, "app.db.pg_connection.execute")
    handler = logger_mod.PostgresLogHandler()
    handler.open_run(
        run_id="r1", project_id="p1", group_id="g1",
        module="estrai", triggered_by="manual", user_id="usr_target",
    )

    _assert_single_insert_user_id(captured_calls, "log_runs", "usr_target", "open_run")


# 4. Scope completo N9 â€” preview, carica, enrichment (+ helper condiviso)


def test_shared_recheck_helper_exists_and_has_correct_contract():
    """Helper recheck_user_authorized centralizzato in app.shared."""
    from app.core.auth.async_user_recheck import recheck_user_authorized

    sig = inspect.signature(recheck_user_authorized)
    assert "user_id" in sig.parameters
    assert "project_id" in sig.parameters
    # Default user_id=None deve far skippare il check (backward compat)
    params = sig.parameters
    assert params["user_id"].annotation == Optional[str] or "Optional" in str(params["user_id"].annotation)


def test_shared_recheck_returns_none_when_user_id_missing():
    """user_id=None (cron/scheduled) -> None (task procede senza check)."""
    from app.core.auth.async_user_recheck import recheck_user_authorized
    result = recheck_user_authorized(user_id=None, project_id="any")
    assert result is None


def test_shared_recheck_returns_permission_revoked_on_disabled_user(monkeypatch):
    """User disabilitato -> return permission_revoked dict."""
    from app.core.auth import async_user_recheck as mod
    from app.core.auth.db import UserRow

    def fake_get_user(uid):
        return UserRow(
            id=uid,
            username="x",
            group_id="g",
            role="user",
            is_active=False,
        )

    # NB: `async_user_recheck.py` importa `get_user` dal leaf canonico
    # `app.db.config.users` AT MODULE LOAD: il symbol `get_user` e' bound nel namespace
    # del modulo helper, non riferito ogni volta all'origine. Patchare
    # l'origine lascia invariato il
    # symbol bound qui â†’ la chiamata reale a `pg_connection.fetchone` parte
    # â†’ access violation se DB non raggiungibile (vedi review #8 Batch B).
    # Pattern simmetrico a `test_n11_canvas_types_propagation_contract.py`
    # (review #7).
    monkeypatch.setattr("app.core.auth.async_user_recheck.get_user", fake_get_user)
    result = mod.recheck_user_authorized(user_id="usr_x", project_id="proj_1")
    assert result is not None
    assert result["status"] == "permission_revoked"
    assert result["success"] is False
    assert "disabilit" in result["message"].lower() or "autorizzat" in result["message"].lower()


@pytest.mark.parametrize("task_attr,task_module", OTHER_USER_TASKS)
def test_other_tasks_accept_user_id_kwarg(task_attr, task_module):
    """preview/carica/enrichment task devono accettare user_id optional."""
    mod = importlib.import_module(task_module)
    task = getattr(mod, task_attr)
    sig = inspect.signature(task)
    assert "user_id" in sig.parameters, (
        f"{task_attr} non accetta user_id kwarg â€” N9 scope incompleto."
    )
    assert sig.parameters["user_id"].default is None


@pytest.mark.parametrize("task_attr,task_module", OTHER_USER_TASKS)
def test_other_tasks_reference_shared_recheck_helper(task_attr, task_module):
    """preview/carica/enrichment task devono referenziare
    `recheck_user_authorized` come callee (single source of truth).

    Replacement R-04 di `inspect.getsource(task) + "recheck_user_authorized" in src`:
    bytecode probe (co_names ricorsivo per closures).
    """
    mod = importlib.import_module(task_module)
    task = getattr(mod, task_attr)
    assert _references_name(task, "recheck_user_authorized"), (
        f"{task_attr} non referenzia recheck_user_authorized â€” rischio drift "
        f"di logica re-check tra i worker."
    )


@pytest.mark.parametrize(
    "service_method,task_attr,task_module,service_kwargs",
    OTHER_USER_SERVICES,
)
def test_other_services_forward_user_id_to_delay(
    monkeypatch, service_method, task_attr, task_module, service_kwargs,
):
    """`create_{preview,carica,enrichment}_task` accettano e forward
    user_id alla task Celery via `.delay()`.

    Replacement R-04 di `inspect.getsource(method) + "user_id" in src`:
    spy su `.delay` per ogni task. Verifica anche signature.
    """
    from app.core.orchestrator.task_service import task_service

    method = _task_service_signature_target(service_method)
    sig = inspect.signature(method)
    assert "user_id" in sig.parameters, (
        f"TaskService.{service_method} non accetta user_id â€” il router "
        f"non puo' propagare l'identita' al worker."
    )

    # Spy su .delay del task corrispondente.
    task_mod = importlib.import_module(task_module)
    task = getattr(task_mod, task_attr)

    captured = {}

    class _FakeAsyncResult:
        id = "celery-1"

    def fake_delay(**kwargs):
        captured.update(kwargs)
        return _FakeAsyncResult()

    monkeypatch.setattr(task, "delay", fake_delay)

    getattr(task_service, service_method)(user_id="usr_y", **service_kwargs)

    assert captured.get("user_id") == "usr_y", (
        f"{service_method} non forward user_id a .delay. "
        f"Captured: {captured}"
    )


def test_migration_for_log_runs_user_id_exists():
    """La migration SQL che aggiunge la colonna user_id deve esistere
    ed essere applicabile (idempotente)."""
    _assert_user_id_migration_exists(
        "log_runs_user_id",
        "ALTER TABLE log_runs",
        "Nessuna migration per log_runs.user_id trovata in {mig_dir}",
    )


# 5. Allineamento terminologico: user_id persistito anche in intermediate.runs


def test_migration_for_intermediate_runs_user_id_exists():
    """La nota originale N9 diceva 'persisti user_id nella run': ci sono
    DUE tabelle run (plenora.log_runs + intermediate.runs). Allineiamo
    entrambe per coerenza. Questa migration copre la seconda."""
    _assert_user_id_migration_exists(
        "intermediate_runs_user_id",
        "ALTER TABLE runs",
        "Nessuna migration per intermediate.runs.user_id in {mig_dir}. "
        "Allineamento fra log_runs e runs incompleto.",
    )


def test_intermediate_create_run_accepts_user_id():
    """intermediate.db.create_run deve accettare user_id optional."""
    from app.core.intermediate.db import create_run

    sig = inspect.signature(create_run)
    assert "user_id" in sig.parameters, (
        "intermediate.db.create_run non accetta user_id â€” allineamento "
        "con log_runs incompleto."
    )
    assert sig.parameters["user_id"].default is None


def test_intermediate_create_run_writes_user_id_in_insert_sql(monkeypatch):
    """L'INSERT su intermediate.runs deve includere la colonna user_id e
    il valore deve apparire nei params bindati.

    Replacement R-04 di `inspect.getsource(create_run) + "user_id" in src + "INSERT INTO runs" + "user_id)"`:
    SQL capture via spy su intermediate.db.execute -> boundary DB.
    """
    from app.core.intermediate import db as int_db

    captured_calls = _capture_execute(monkeypatch, "app.core.intermediate.db.execute")
    # Stub get_run per evitare fetch reale dopo l'INSERT.
    monkeypatch.setattr(int_db, "get_run", lambda run_id: {"run_id": run_id})

    int_db.create_run(
        run_id="r-1", canvas_id="canvas-1", group_id="g-1",
        total_nodes=3, user_id="usr_intermediate",
    )

    _assert_single_insert_user_id(
        captured_calls,
        "runs",
        "usr_intermediate",
        "create_run",
    )


@pytest.mark.parametrize("task_name,task_module", [
    ("execute_pipeline_task", "workers.tasks.pipeline_task"),
    ("execute_enrichment_task", "workers.tasks.enrichment_task"),
])
def test_tasks_reference_intermediate_create_run(task_name, task_module):
    """I task che aprono una run intermediate devono referenziare
    `create_run` come callee (carica non chiama create_run intermediate).

    Replacement R-04 di `inspect.getsource(task) + "create_run" in src + "user_id=user_id" in src`:
    bytecode probe per il callee identifier. Lo specifico kwarg
    propagation user_id e' coperto dal test e2e sotto.
    """
    mod = importlib.import_module(task_module)
    task = getattr(mod, task_name)
    assert _references_name(task, "create_run"), (
        f"{task_name} non referenzia intermediate.db.create_run â€” "
        "allineamento incompleto (log_runs ha user_id, intermediate.runs no)."
    )


def test_pipeline_task_passes_user_id_to_intermediate_create_run(monkeypatch):
    """Il task `execute_pipeline_task` deve invocare
    `intermediate.db.create_run` propagando user_id come kwarg.

    Replacement R-04 (parte e2e di) `inspect.getsource(...) + "user_id=user_id" in src`:
    eager mode + spy su `create_run`. Scope: solo pipeline_task; per
    enrichment_task il pattern e' lo stesso ma il task body e' grosso
    (tested via bytecode probe `test_tasks_reference_intermediate_create_run`).
    """
    pt = _patch_pipeline_eager_success(monkeypatch)

    # Logger no-op.
    monkeypatch.setattr("app.utils.logger.open_run", lambda **kwargs: None)
    monkeypatch.setattr("app.utils.logger.close_run", lambda **kwargs: None)

    # Spy su intermediate.db.create_run.
    captured_create_run = {}

    def spy_create_run(*args, **kwargs):
        # Cattura sia args posizionali sia kwargs.
        captured_create_run["args"] = args
        captured_create_run["kwargs"] = kwargs
        return {"run_id": kwargs.get("run_id") or (args[0] if args else "r-1")}

    monkeypatch.setattr(
        "app.core.intermediate.run_lifecycle.create_run", spy_create_run,
    )

    pt.execute_pipeline_task.apply(
        kwargs={
            "project_id": "proj_1",
            "user_id": "usr_x",
            "run_id": "r-1",
        },
    )

    # Il task puo' passare user_id come kwarg o come positional finale.
    propagated = captured_create_run.get("kwargs", {}).get("user_id")
    if propagated is None:
        # Fallback: positional. La signature ha user_id all'ultimo posto.
        args = captured_create_run.get("args", ())
        if len(args) >= 5:
            propagated = args[4]
    assert propagated == "usr_x", (
        "execute_pipeline_task non propaga user_id a intermediate.create_run. "
        "L'audit trail (intermediate.runs.user_id) sara' incompleto. "
        f"Captured: {captured_create_run}"
    )
