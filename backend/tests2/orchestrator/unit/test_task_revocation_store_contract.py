"""Contratti dello store di annullamento task (Plenora 2.0, Fase 5 F5.2).

Annullamento cooperativo su PostgreSQL (rimpiazzo di celery
app.control.revoke). La semantica e' blindata sull'InMemory (lane no-DB) +
forma SQL del backend PG; il comportamento reale e' verificato e2e.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.orchestrator.task_revocation_store import (
    InMemoryTaskRevocationStore,
    PgTaskRevocationStore,
    TaskRevocationStore,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


# InMemory (semantica di riferimento)


def test_in_memory_satisfies_protocol():
    assert isinstance(InMemoryTaskRevocationStore(), TaskRevocationStore)


def test_revoke_then_is_revoked():
    store = InMemoryTaskRevocationStore()
    assert store.is_revoked("t1") is False
    store.revoke("t1")
    assert store.is_revoked("t1") is True


def test_revoke_is_isolated_per_task():
    store = InMemoryTaskRevocationStore()
    store.revoke("t1")
    assert store.is_revoked("t1") is True
    assert store.is_revoked("t2") is False


def test_clear_removes_flag():
    store = InMemoryTaskRevocationStore()
    store.revoke("t1")
    store.clear("t1")
    assert store.is_revoked("t1") is False


# Backend PostgreSQL (forma SQL; lane no-DB)


def test_pg_store_satisfies_protocol():
    assert isinstance(PgTaskRevocationStore(), TaskRevocationStore)


def test_pg_revoke_is_ttl_upsert():
    src = inspect.getsource(PgTaskRevocationStore.revoke)
    assert "INSERT INTO task_revocations" in src
    assert "ON CONFLICT (task_id) DO UPDATE" in src
    assert "interval '1 second'" in src


def test_pg_is_revoked_filters_expired_and_fails_soft():
    src = inspect.getsource(PgTaskRevocationStore.is_revoked)
    assert "expires_at IS NULL OR expires_at > now()" in src
    # Fail-soft: sul dubbio NON revocato (un errore non deve cancellare un
    # task valido).
    assert "return False" in src


def test_pg_cleanup_deletes_expired():
    src = inspect.getsource(PgTaskRevocationStore.cleanup_expired)
    assert "DELETE FROM task_revocations" in src
    assert "expires_at <= now()" in src
