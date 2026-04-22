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
- `_current_group_id` azzerato PRIMA del path che puo' fallire
  (no stato stale sul singleton).
- ramo `_proj_row is None` INVARIATO (regression guard esplicito).
- chiamata a `_init_execution_context` spostata dentro il `try`
  di `execute_enrichment` -> `ExecutionContextError` viene raccolto
  dal boundary Fix-C e trasformato in response safe con
  `user_error=False`.
"""

import psycopg2
import pytest

from app.core.arricchisci import api_connector as api_connector_mod
from app.shared.exceptions import ExecutionContextError


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.regression,
]


def test_init_execution_context_sets_group_id_on_happy_path(api_service, monkeypatch):
    """Path nominale: progetto esistente con `group_id` ->
    `_current_group_id` settato, `_preview_limit` settato.
    """
    monkeypatch.setattr(
        api_connector_mod.db_svc,
        "get_project",
        lambda project_id: {"id": project_id, "group_id": "grp-A", "name": "p1"},
    )

    api_service._init_execution_context("proj-1", preview_limit=100)

    assert api_service._current_group_id == "grp-A"
    assert api_service._preview_limit == 100


def test_init_execution_context_preserves_null_group_on_missing_project(
    api_service, monkeypatch
):
    """Regression guard: progetto inesistente -> `_current_group_id=None`
    senza eccezione. E' ramo dati, non errore. Il fail-closed downstream
    ("Contesto di sicurezza non determinabile") e' coperto da
    `test_api_connector_security_contract.py` e
    `test_enrichment_task_tenant_binding_lock.py` -- se questo test
    si rompe, Fix-B ha toccato per errore il ramo dati.
    """
    monkeypatch.setattr(
        api_connector_mod.db_svc,
        "get_project",
        lambda project_id: None,
    )

    api_service._init_execution_context("proj-unknown", preview_limit=None)

    assert api_service._current_group_id is None
    assert api_service._preview_limit is None


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


def test_init_execution_context_resets_stale_group_id_before_db_call(
    api_service, monkeypatch
):
    """Vincolo C3: `_current_group_id` azzerato PRIMA del path che
    puo' fallire. Se il singleton aveva uno stato residuo da una
    chiamata precedente e la nuova solleva `ExecutionContextError`,
    non deve restare un `group_id` stale che un check downstream
    ritenga valido. Questo test lock il vincolo.
    """
    # Simula stato residuo da una call precedente.
    api_service._current_group_id = "grp-STALE"

    def _boom(project_id):
        raise psycopg2.OperationalError("connection lost")

    monkeypatch.setattr(api_connector_mod.db_svc, "get_project", _boom)

    with pytest.raises(ExecutionContextError):
        api_service._init_execution_context("proj-new", preview_limit=None)

    assert api_service._current_group_id is None, (
        "_current_group_id non azzerato prima del path che puo' fallire "
        "(rischio tenant-leak su singleton)"
    )


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
