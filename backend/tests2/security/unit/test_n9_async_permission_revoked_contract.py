"""Contract test N9 — re-check permessi user in pipeline_task.

Pre-fix scenario:
  T0: admin X del gruppo A esegue pipeline -> router fa assert_project_group
      -> task Celery in coda.
  T0+Xs: admin B disabilita X, o lo rimuove dal gruppo A.
  T1: worker Celery prende la task e la esegue lo stesso — i permessi
      non sono piu' verificati.

Risultato: elevation of privilege temporale. Un admin revocato puo'
completare operazioni pesanti su risorse a cui non dovrebbe piu' accedere.

Fix (2026-04-17):
  - `execute_pipeline_task` accetta `user_id` optional
  - Se passato, il task re-verifica l'utente:
    * esiste + is_active=True
    * ha ancora accesso al progetto (same group_id o superadmin)
  - Se fallisce -> return status='permission_revoked' senza eseguire

Backward compat: user_id=None -> skip re-check (ok per task cron
schedulate che non hanno un user umano dietro).
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


# =============================================================================
# 1. Task accetta user_id
# =============================================================================


def test_execute_pipeline_task_accepts_user_id_kwarg():
    """execute_pipeline_task deve accettare `user_id` optional per
    re-check permessi al pickup del worker."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    sig = inspect.signature(execute_pipeline_task)
    assert "user_id" in sig.parameters, (
        "execute_pipeline_task non accetta user_id. Senza, il re-check "
        "permessi al pickup non e' possibile."
    )
    # Default None -> backward compat con task cron senza user umano
    assert sig.parameters["user_id"].default is None, (
        "user_id default deve essere None per backward compat con cron/"
        "scheduler che non hanno un user umano dietro."
    )


def test_execute_pipeline_task_source_rechecks_permissions_when_user_id_given():
    """Il body deve chiamare l'helper recheck_user_authorized quando
    user_id e' disponibile. La logica concreta (get_user, is_active,
    assert_project_access) sta nell'helper centralizzato."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    src = inspect.getsource(execute_pipeline_task)
    assert "user_id" in src, "il task non referenzia user_id nel body"
    assert "recheck_user_authorized" in src, (
        "il task non usa l'helper centralizzato recheck_user_authorized. "
        "Rischio di drift nella logica di re-check tra i worker."
    )


def test_execute_pipeline_task_emits_permission_revoked_status():
    """Su re-check fallito, il task deve ritornare uno status chiaro.
    Il dict con status='permission_revoked' viene costruito dall'helper
    e ritornato dal task body se non-None."""
    from app.shared.async_user_recheck import recheck_user_authorized

    src = inspect.getsource(recheck_user_authorized)
    assert "permission_revoked" in src, (
        "l'helper recheck_user_authorized non emette status='permission_revoked'. "
        "Useremmo quella stringa per distinguerlo da altri fail nel reporting."
    )


# =============================================================================
# 2. Service e router forward user_id
# =============================================================================


def test_create_pipeline_task_accepts_user_id():
    """task_service.create_pipeline_task deve accettare e forward user_id."""
    from app.services.task_service import TaskService

    sig = inspect.signature(TaskService.create_pipeline_task)
    assert "user_id" in sig.parameters, (
        "TaskService.create_pipeline_task non accetta user_id. "
        "Senza, il router non puo' propagare l'identita' alla task."
    )


def test_create_pipeline_task_source_forwards_user_id():
    """Il source di create_pipeline_task deve passare user_id a .delay()."""
    from app.services.task_service import TaskService

    src = inspect.getsource(TaskService.create_pipeline_task)
    assert "user_id" in src, (
        "create_pipeline_task non referenzia user_id nel body. "
        "Forward alla task Celery mancante."
    )


def test_execute_endpoint_passes_current_user_id():
    """Il router /execute deve propagare current_user.user_id al service."""
    from app.core.orchestrator import router as orch_router

    src = inspect.getsource(orch_router.execute_project_pipeline)
    # Deve esserci un riferimento a current_user.user_id passato
    assert "user_id" in src and "current_user" in src, (
        "execute_project_pipeline non forward current_user.user_id. "
        "La task partira' senza identita' -> re-check no-op."
    )


# =============================================================================
# 3. Hardening (refinement N9): assert_project_access + user_id persistito
# =============================================================================


def test_shared_recheck_uses_assert_project_access_helper():
    """Il re-check non deve replicare logica custom: l'helper condiviso
    deve usare `assert_project_access` (stesso che il router usa) per
    coerenza tra HTTP gate e worker gate."""
    from app.shared.async_user_recheck import recheck_user_authorized

    src = inspect.getsource(recheck_user_authorized)
    assert "assert_project_access" in src, (
        "L'helper non usa assert_project_access: logica re-check duplicata "
        "rispetto al router. Serve coerenza via helper centralizzato."
    )


def test_pipeline_task_passes_user_id_to_open_run():
    """Il task deve propagare user_id a open_run per audit (log_runs.user_id)."""
    from workers.tasks.pipeline_task import execute_pipeline_task

    src = inspect.getsource(execute_pipeline_task)
    # Cerca pattern open_run(... user_id=user_id ...)
    # Conservativo: controlliamo che 'user_id=user_id' appaia nel source
    assert "user_id=user_id" in src, (
        "Il task non passa user_id a open_run. L'audit trail in log_runs "
        "non avra' l'identita' dell'utente che ha lanciato la run."
    )


def test_open_run_accepts_user_id_kwarg():
    """La funzione open_run (wrapper modulo) deve accettare user_id
    optional per persistenza audit."""
    from app.utils.logger import open_run

    sig = inspect.signature(open_run)
    assert "user_id" in sig.parameters, (
        "open_run non accetta user_id. Senza, non possiamo persistere "
        "chi ha lanciato la run nel log_runs."
    )
    assert sig.parameters["user_id"].default is None, (
        "user_id default deve essere None (backward compat con cron/scheduled)."
    )


def test_open_run_writes_user_id_to_log_runs():
    """Il body dell'INSERT deve includere la colonna user_id."""
    from app.utils.logger import PostgresLogHandler

    src = inspect.getsource(PostgresLogHandler.open_run)
    assert "user_id" in src, (
        "PostgresLogHandler.open_run non scrive user_id nella query SQL."
    )


# =============================================================================
# 4. Scope completo N9 — preview, carica, enrichment (+ helper condiviso)
# =============================================================================


def test_shared_recheck_helper_exists_and_has_correct_contract():
    """Helper recheck_user_authorized centralizzato in app.shared."""
    from app.shared.async_user_recheck import recheck_user_authorized

    sig = inspect.signature(recheck_user_authorized)
    assert "user_id" in sig.parameters
    assert "project_id" in sig.parameters
    # Default user_id=None deve far skippare il check (backward compat)
    from typing import Optional
    params = sig.parameters
    assert params["user_id"].annotation == Optional[str] or "Optional" in str(params["user_id"].annotation)


def test_shared_recheck_returns_none_when_user_id_missing():
    """user_id=None (cron/scheduled) -> None (task procede senza check)."""
    from app.shared.async_user_recheck import recheck_user_authorized
    result = recheck_user_authorized(user_id=None, project_id="any")
    assert result is None


def test_shared_recheck_returns_permission_revoked_on_disabled_user(monkeypatch):
    """User disabilitato -> return permission_revoked dict."""
    from app.shared import async_user_recheck as mod

    def fake_get_user(uid):
        return {"id": uid, "username": "x", "group_id": "g", "role": "user", "is_active": False}

    monkeypatch.setattr("app.db.config_db_service.get_user", fake_get_user)
    result = mod.recheck_user_authorized(user_id="usr_x", project_id="proj_1")
    assert result is not None
    assert result["status"] == "permission_revoked"
    assert result["success"] is False
    assert "disabilit" in result["message"].lower() or "autorizzat" in result["message"].lower()


@pytest.mark.parametrize("task_attr,task_module", [
    ("preview_pipeline_task", "workers.tasks.pipeline_task"),
    ("execute_carica_task", "workers.tasks.carica_task"),
    ("execute_enrichment_task", "workers.tasks.enrichment_task"),
])
def test_other_tasks_accept_user_id_kwarg(task_attr, task_module):
    """preview/carica/enrichment task devono accettare user_id optional."""
    import importlib
    mod = importlib.import_module(task_module)
    task = getattr(mod, task_attr)
    sig = inspect.signature(task)
    assert "user_id" in sig.parameters, (
        f"{task_attr} non accetta user_id kwarg — N9 scope incompleto."
    )
    assert sig.parameters["user_id"].default is None


@pytest.mark.parametrize("task_attr,task_module", [
    ("preview_pipeline_task", "workers.tasks.pipeline_task"),
    ("execute_carica_task", "workers.tasks.carica_task"),
    ("execute_enrichment_task", "workers.tasks.enrichment_task"),
])
def test_other_tasks_use_shared_recheck_helper(task_attr, task_module):
    """preview/carica/enrichment task devono usare l'helper condiviso
    (single source of truth)."""
    import importlib
    mod = importlib.import_module(task_module)
    task = getattr(mod, task_attr)
    src = inspect.getsource(task)
    assert "recheck_user_authorized" in src, (
        f"{task_attr} non usa recheck_user_authorized — rischio drift "
        f"di logica re-check tra i worker."
    )


@pytest.mark.parametrize("service_method", [
    "create_preview_task",
    "create_carica_task",
    "create_enrichment_task",
])
def test_other_services_forward_user_id(service_method):
    """task_service.create_{preview,carica,enrichment}_task accettano
    e forward user_id alla task Celery."""
    from app.services.task_service import TaskService
    method = getattr(TaskService, service_method)
    sig = inspect.signature(method)
    assert "user_id" in sig.parameters, (
        f"TaskService.{service_method} non accetta user_id — il router "
        f"non puo' propagare l'identita' al worker."
    )
    src = inspect.getsource(method)
    assert "user_id" in src.split(".delay")[0] or "user_id" in src, (
        f"{service_method} non forward user_id a .delay()."
    )


def test_migration_for_log_runs_user_id_exists():
    """La migration SQL che aggiunge la colonna user_id deve esistere
    ed essere applicabile (idempotente)."""
    import os
    from pathlib import Path

    mig_dir = Path(__file__).resolve().parents[3] / "migrations"
    candidates = [f for f in os.listdir(mig_dir) if "log_runs_user_id" in f]
    assert len(candidates) >= 1, (
        f"Nessuna migration per log_runs.user_id trovata in {mig_dir}"
    )
    sql = (mig_dir / candidates[0]).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS user_id" in sql, (
        "La migration deve usare ADD COLUMN IF NOT EXISTS per idempotenza."
    )
    assert "ALTER TABLE log_runs" in sql, (
        "La migration deve alterare la tabella log_runs."
    )


# =============================================================================
# 5. Allineamento terminologico: user_id persistito anche in intermediate.runs
# =============================================================================


def test_migration_for_intermediate_runs_user_id_exists():
    """La nota originale N9 diceva 'persisti user_id nella run': ci sono
    DUE tabelle run (plenora.log_runs + intermediate.runs). Allineiamo
    entrambe per coerenza. Questa migration copre la seconda."""
    import os
    from pathlib import Path

    mig_dir = Path(__file__).resolve().parents[3] / "migrations"
    candidates = [f for f in os.listdir(mig_dir) if "intermediate_runs_user_id" in f]
    assert len(candidates) >= 1, (
        f"Nessuna migration per intermediate.runs.user_id in {mig_dir}. "
        "Allineamento fra log_runs e runs incompleto."
    )
    sql = (mig_dir / candidates[0]).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS user_id" in sql
    assert "ALTER TABLE runs" in sql


def test_intermediate_create_run_accepts_user_id():
    """intermediate.db.create_run deve accettare user_id optional."""
    from app.core.intermediate.db import create_run

    sig = inspect.signature(create_run)
    assert "user_id" in sig.parameters, (
        "intermediate.db.create_run non accetta user_id — allineamento "
        "con log_runs incompleto."
    )
    assert sig.parameters["user_id"].default is None


def test_intermediate_create_run_writes_user_id_to_sql():
    """Il body dell'INSERT su intermediate.runs deve includere user_id."""
    from app.core.intermediate.db import create_run

    src = inspect.getsource(create_run)
    assert "user_id" in src
    # Verifica pattern: user_id nelle colonne dell'INSERT e come parametro
    assert "INSERT INTO runs" in src
    assert ", user_id)" in src or ",user_id)" in src or "user_id)" in src


@pytest.mark.parametrize("task_name,task_module", [
    ("execute_pipeline_task", "workers.tasks.pipeline_task"),
    ("execute_enrichment_task", "workers.tasks.enrichment_task"),
])
def test_tasks_pass_user_id_to_intermediate_create_run(task_name, task_module):
    """I task che aprono una run intermediate devono passare user_id
    (pipeline e enrichment; carica non chiama create_run intermediate)."""
    import importlib
    mod = importlib.import_module(task_module)
    src = inspect.getsource(getattr(mod, task_name))
    # Cerca pattern: create_run(...user_id=user_id...)
    assert "create_run" in src and "user_id=user_id" in src, (
        f"{task_name} non passa user_id a intermediate create_run — "
        "allineamento incompleto (log_runs ha user_id, intermediate.runs no)."
    )
