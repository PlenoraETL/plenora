"""Contratti dello store stato/esito task (Plenora 2.0, Fase 2).

Lo store rimpiazza il result backend Redis di Celery. La lane e' no-DB:
qui si blinda la semantica su ``InMemoryTaskResultStore`` e la forma SQL
del backend PostgreSQL via check source-bound (il comportamento PG reale
e' verificato e2e sullo stack). La payload segue la semantica Celery
``AsyncResult.info == .result``.
"""

from __future__ import annotations

import inspect
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.orchestrator.task_result_store import (
    InMemoryTaskResultStore,
    PENDING_STATE,
    PgTaskResultStore,
    TaskResultRecord,
    default_task_result_store,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.orchestrator,
    pytest.mark.contract,
]


# Semantica (InMemory test double)


def test_put_then_get_roundtrips_state_name_and_payload():
    store = InMemoryTaskResultStore()
    store.put(
        "task-1",
        state="PROGRESS",
        payload={"progress": 42, "message": "lavoro", "is_error": False},
        name="carica.execute",
    )

    record = store.get("task-1")

    assert record == TaskResultRecord(
        task_id="task-1",
        state="PROGRESS",
        name="carica.execute",
        payload={"progress": 42, "message": "lavoro", "is_error": False},
    )


def test_get_unknown_task_returns_none():
    assert InMemoryTaskResultStore().get("mai-visto") is None


def test_progress_write_without_name_preserves_existing_name():
    # Parita' col COALESCE PG: un update di progress che non porta il nome
    # non deve azzerare il nome registrato alla prima scrittura.
    store = InMemoryTaskResultStore()
    store.put("t", state="STARTED", name="pipeline.execute")
    store.put("t", state="PROGRESS", payload={"progress": 10})

    record = store.get("t")
    assert record is not None
    assert record.name == "pipeline.execute"
    assert record.state == "PROGRESS"


def test_finalize_overwrites_payload_with_result():
    store = InMemoryTaskResultStore()
    store.put("t", state="PROGRESS", payload={"progress": 90}, name="carica.execute")
    store.put("t", state="SUCCESS", payload={"success": True, "rows_written": 5})

    record = store.get("t")
    assert record is not None
    assert record.state == "SUCCESS"
    assert record.payload == {"success": True, "rows_written": 5}


def test_forget_removes_record():
    store = InMemoryTaskResultStore()
    store.put("t", state="SUCCESS", payload={"success": True})
    store.forget("t")
    assert store.get("t") is None


def test_in_memory_payload_is_copied_not_aliased():
    store = InMemoryTaskResultStore()
    original = {"progress": 1}
    store.put("t", state="PROGRESS", payload=original)
    original["progress"] = 999

    record = store.get("t")
    assert record is not None
    assert record.payload == {"progress": 1}


def test_in_memory_payload_is_json_safe_for_decimal_values():
    store = InMemoryTaskResultStore()
    store.put(
        "t",
        state="SUCCESS",
        payload={"rows": [{"amount": Decimal("42.5000")}]},
    )

    record = store.get("t")

    assert record is not None
    assert record.payload == {"rows": [{"amount": "42.5"}]}


# Forma SQL del backend PostgreSQL (source-bound)


def test_pg_put_is_upsert_on_task_id_with_ttl_expiry():
    src = inspect.getsource(PgTaskResultStore.put)
    assert "INSERT INTO task_results" in src
    assert "ON CONFLICT (task_id) DO UPDATE" in src
    # TTL espresso come intervallo a partire da now() (rimpiazzo di
    # TASK_RESULT_EXPIRES gestito prima da Redis).
    assert "interval '1 second'" in src
    # name preservato sugli update parziali (progress senza nome).
    assert "COALESCE(EXCLUDED.name, task_results.name)" in src


def test_pg_put_serializes_decimal_payload_before_jsonb_write(monkeypatch):
    import app.core.orchestrator.task_result_store as module

    captured = {}

    def fake_execute(_sql, params):
        captured["payload_json"] = params[3]

    monkeypatch.setattr(module.pg_connection, "execute", fake_execute)

    PgTaskResultStore().put(
        "task-preview",
        state="SUCCESS",
        payload={"rows": [{"amount": Decimal("42.5000")}]},
    )

    assert json.loads(captured["payload_json"]) == {
        "rows": [{"amount": "42.5"}],
    }


def test_pg_get_filters_out_expired_rows():
    src = inspect.getsource(PgTaskResultStore.get)
    assert "expires_at IS NULL OR expires_at > now()" in src


def test_pg_cleanup_deletes_only_expired():
    src = inspect.getsource(PgTaskResultStore.cleanup_expired)
    assert "DELETE FROM task_results" in src
    assert "expires_at IS NOT NULL AND expires_at <= now()" in src


# Default runtime


def test_default_store_is_postgres_backed():
    store = default_task_result_store()
    assert isinstance(store, PgTaskResultStore)


def test_pending_state_matches_celery_unknown_task_default():
    # Un task_id sconosciuto in Celery e' PENDING: l'adapter di lettura
    # (Fase 2 cutover) si appoggia a questa costante per le righe assenti.
    assert PENDING_STATE == "PENDING"


def test_module_has_no_redis_dependency():
    import app.core.orchestrator.task_result_store as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    # PostgreSQL-only (Fase 2): nessun import di client/store Redis. La
    # parola "Redis" puo' comparire nei commenti (era il result backend
    # Celery/Redis, rimpiazzato in Fase 2), ma non come dipendenza importata.
    lowered = source.lower()
    assert "import redis" not in lowered
    assert "rediskeyvaluestore" not in lowered
    assert "redis_store" not in lowered
    # Il default deve essere PostgreSQL, non un fallback.
    assert "return PgTaskResultStore(" in source
    # json usato per serializzare la payload sul backend PG.
    assert json  # import attivo
