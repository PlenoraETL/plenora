"""Regression guards per `_init_execution_context` fail-closed su errori DB.

Batch 3 Fix-B (audit `arricchisci/api_connector` §7.2 + checkpoint C3).

Pre-fix: catch broad `Exception` -> `logger.warning` +
`_current_group_id = None`, fail-soft delegato a check downstream
che riportavano "Contesto di sicurezza non determinabile"
indistinguibile da progetto inesistente.

Post-fix:
- catch ristretto a `psycopg2.Error`.
- re-raise come `ExecutionContextError` con chain preservata via
  `from e`.
- ramo `_proj_row is None` INVARIATO (regression guard esplicito).
- chiamata a `_init_execution_context` spostata dentro il `try`
  di `execute_enrichment` -> `ExecutionContextError` viene raccolto
  dal boundary Fix-C e trasformato in response safe con
  `user_error=False`.

BE-GO-READY-ORCHESTRATION-ENGINE-011-CANVAS-CTX-NO-LEGACY-STATE-CUT
+ BE-GO-READY-ORCHESTRATION-ENGINE-012-KILL-APPLY-TO-LEGACY-STATE:
`init_execution_context` no longer mutates
`svc._current_group_id` / `svc._preview_limit`. Post-OE012 the
legacy mirror function (`apply_to_legacy_state`) has been
deleted; production mixin readers are all ctx-strict. The
tests below now assert the TYPED `ctx` return value carries the
right tenant binding; the negative source-bound lock in
`test_external_consumers_ctx_migration_contract.py` enforces
that the helper is never re-introduced.
"""

import psycopg2
import pytest

from app.core.arricchisci import api_connector as api_connector_mod
from app.core.arricchisci import canvas_orchestrator as canvas_orchestrator_mod
from app.shared.exceptions import ExecutionContextError


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.regression,
]


def test_init_execution_context_returns_ctx_with_group_id_on_happy_path(
    api_service, monkeypatch,
):
    """Path nominale: progetto esistente con `group_id` ->
    `init_execution_context` returns a typed
    `ArricchisciExecutionContext` carrying the tenant binding.

    Post-OE011: the function no longer writes singleton state.
    The typed `ctx` IS the contract; downstream mixins consume
    it via explicit `ctx=ctx` kwargs (locked elsewhere).
    """
    monkeypatch.setattr(
        api_connector_mod.db_svc,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-A", "name": "p1"},
    )

    ctx = canvas_orchestrator_mod.init_execution_context(
        api_service, "proj-1", preview_limit=100,
    )

    assert ctx.project_id == "proj-1"
    assert ctx.group_id == "grp-A"
    assert ctx.preview_limit == 100


def test_init_execution_context_returns_ctx_with_null_group_on_missing_project(
    api_service, monkeypatch
):
    """Regression guard: progetto inesistente -> `ctx.group_id = None`
    senza eccezione. E' ramo dati, non errore. Il fail-closed
    downstream ("Contesto di sicurezza non determinabile") e'
    coperto da `test_api_connector_security_contract.py` e
    `test_enrichment_task_tenant_binding_lock.py` -- se questo
    test si rompe, abbiamo toccato per errore il ramo dati.

    Post-OE011: assertion moved from `svc._current_group_id` to
    `ctx.group_id` (the typed boundary).
    """
    monkeypatch.setattr(
        api_connector_mod.db_svc,
        "get_project",
        lambda project_id: None,
    )

    ctx = canvas_orchestrator_mod.init_execution_context(
        api_service, "proj-unknown", preview_limit=None,
    )

    assert ctx.project_id == "proj-unknown"
    assert ctx.group_id is None
    assert ctx.preview_limit is None


def test_init_execution_context_raises_execution_context_error_on_db_error(
    api_service, monkeypatch
):
    """DB down / pool exhausted / deadlock non viene piu' degradato
    a `group_id=None` (pre-fix): viene re-raised come
    `ExecutionContextError` con `original_error` + `__cause__`
    impostati all'eccezione psycopg2 originale (chain preservata).
    """
    original = psycopg2.OperationalError("pool exhausted")

    def _boom(project_id):
        raise original

    monkeypatch.setattr(api_connector_mod.db_svc, "get_project", _boom)

    with pytest.raises(ExecutionContextError) as exc_info:
        api_service._init_execution_context("proj-1", preview_limit=None)

    err = exc_info.value
    assert err.project_id == "proj-1"
    assert "DB" in err.reason  # intent: messaggio categoria DB
    assert err.original_error is original
    assert err.__cause__ is original  # `from e` preservato


def test_init_execution_context_does_not_write_singleton_state_on_db_error(
    api_service, monkeypatch
):
    """Post-OE011 contract: `init_execution_context` does NOT
    write `svc._current_group_id` / `svc._preview_limit` on the
    error path. The pre-OE011 "azzera prima del DB lookup"
    invariant was specifically to defend against the legacy
    singleton-state read path; with the production mixins
    ctx-strict, the singleton state is irrelevant -- a stale
    value cannot leak into the new run because no production
    reader consults it.

    The regression we DO still protect: a stale singleton MUST
    NOT mask the typed `ctx` failure. Since the function now
    raises `ExecutionContextError` on `psycopg2.Error` (chain
    preserved) without touching the singleton, the stale
    attribute survives intact -- harmless because nothing reads
    it. We assert the exception still propagates correctly.
    """
    # Simulate residual state from a previous call.
    api_service._current_group_id = "grp-STALE"
    api_service._preview_limit = 999

    def _boom(project_id):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(api_connector_mod.db_svc, "get_project", _boom)

    with pytest.raises(ExecutionContextError):
        canvas_orchestrator_mod.init_execution_context(
            api_service, "proj-new", preview_limit=None,
        )

    # Post-OE011: the singleton is NOT touched. Stale state
    # remains -- and that's fine, because nothing reads it.
    # The invariant is "ctx is the only tenant binding"; locked
    # by `test_init_execution_context_does_not_write_singleton_state`
    # in `test_arricchisci_execution_context_contract.py`.
    assert api_service._current_group_id == "grp-STALE"
    assert api_service._preview_limit == 999


def test_execute_enrichment_routes_db_error_to_safe_boundary_response(
    api_service, monkeypatch
):
    """Integrazione Fix-B + Fix-C (Opzione A checkpoint C3): la chiamata
    a `_init_execution_context` e' DENTRO il `try` di
    `execute_enrichment`. `ExecutionContextError` (non `ValueError`)
    viene catturato dal ramo `except Exception` e tradotto in response
    safe `{success:False, user_error:False, error:"Errore interno..."}`.

    Se il lock si rompe, significa che la chiamata e' tornata FUORI
    dal try e l'eccezione propaga al caller Celery -> response non safe.
    """

    def _boom(project_id):
        raise psycopg2.OperationalError("db down")

    monkeypatch.setattr(api_connector_mod.db_svc, "get_project", _boom)

    result = api_service.execute_enrichment(
        project_id="proj-1",
        canvas={"nodes": [], "edges": []},
    )

    assert result == {
        "success": False,
        "user_error": False,
        "error": "Errore interno durante l'arricchimento",
    }
