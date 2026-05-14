"""
Contract test N15 — idempotency guard in CaricaExecutor.execute().

Scenario:
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.executor import CaricaExecutor
from app.core.carica.models import WriteStatus


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. WriteStatus enum include SKIPPED_IDEMPOTENT


def test_write_status_has_skipped_idempotent():
    """Il nuovo status deve esistere. Callers downstream (scheduler) lo
    accettano come success — vedi test sotto."""
    assert hasattr(WriteStatus, "SKIPPED_IDEMPOTENT")
    assert WriteStatus.SKIPPED_IDEMPOTENT.value == "skipped_idempotent"


# 2. Signature: run_id param present + default None


def test_execute_signature_accepts_run_id():
    sig = inspect.signature(CaricaExecutor.execute)
    assert "run_id" in sig.parameters
    assert sig.parameters["run_id"].default is None


# 3. run_id + already_completed -> short-circuit


def test_execute_short_circuits_when_run_id_already_completed(monkeypatch):
    """Guard scatta: nessuna connessione, nessuna scrittura, result
    ha status SKIPPED_IDEMPOTENT."""
    executor = CaricaExecutor(connection_service=MagicMock())

    # Force is_already_completed -> True
    import app.core.intermediate.run_idempotency as idem_mod
    monkeypatch.setattr(idem_mod, "is_already_completed", lambda rid: True)

    # Se il guard funziona, _get_connection NON deve essere chiamato
    get_conn_called = []
    monkeypatch.setattr(
        executor, "_get_connection",
        lambda cid: get_conn_called.append(cid) or {"type": "postgres"}
    )

    df = pd.DataFrame({"id": [1, 2]})
    node_data = {"connection_id": "conn-1", "table_name": "t", "write_mode": "append"}

    result = executor.execute(
        df=df, node_data=node_data, project_id="p1",
        run_id="already-done-run-id",
    )

    assert result.status == WriteStatus.SKIPPED_IDEMPOTENT
    assert get_conn_called == [], (
        "Guard non ha corto-circuitato: _get_connection e' stato chiamato."
    )


def test_execute_short_circuit_result_has_idempotent_flag():
    """WriteResult dello skip deve essere distinguibile da un success."""
    executor = CaricaExecutor(connection_service=MagicMock())

    with patch("app.core.intermediate.run_idempotency.is_already_completed", return_value=True):
        result = executor.execute(
            df=pd.DataFrame({"x": [1]}),
            node_data={"connection_id": "c", "table_name": "t"},
            run_id="done",
        )

    assert result.status == WriteStatus.SKIPPED_IDEMPOTENT
    # rows_written = 0 perche' non abbiamo scritto nulla in questa run
    assert result.rows_written == 0 or result.rows_written is None


# 4. run_id + NOT completed -> flow normale (guard no-op)


def test_execute_proceeds_when_run_id_not_completed(monkeypatch):
    """Guard non scatta: il flusso procede normalmente (fino al primo
    errore non-guard, che ci aspettiamo in questo test dato che il mock
    non imita tutto lo stack)."""
    executor = CaricaExecutor(connection_service=MagicMock())

    with patch("app.core.intermediate.run_idempotency.is_already_completed", return_value=False):
        # Mock _get_connection cosi' vediamo che viene chiamato
        conn_calls = []
        monkeypatch.setattr(
            executor, "_get_connection",
            lambda cid: conn_calls.append(cid) or {"type": "postgres"}
        )
        # Mock _create_engine cosi' il flow puo' proseguire oltre
        monkeypatch.setattr(executor, "_create_engine", lambda conn: MagicMock())

        df = pd.DataFrame({"id": [1]})
        node_data = {"connection_id": "conn-1", "table_name": "t"}

        # Puo' fallire dopo il guard (validation, writer creation, etc.)
        # ma DEVE aver chiamato _get_connection — prova che il guard NON ha skippato.
        try:
            executor.execute(
                df=df, node_data=node_data, project_id="p",
                run_id="not-done-run-id",
            )
        except Exception:
            pass  # ci aspettiamo fallimento downstream, non ci interessa

        assert conn_calls, (
            "Guard ha corto-circuitato erroneamente: _get_connection non chiamata "
            "ma run_id non era completato."
        )


# 5. Nessun run_id -> guard no-op (retro-compat con callers esistenti)


def test_execute_without_run_id_skips_guard(monkeypatch):
    """Chiamata senza run_id -> is_already_completed NON deve essere invocata."""
    executor = CaricaExecutor(connection_service=MagicMock())

    check_calls = []
    monkeypatch.setattr(
        "app.core.intermediate.run_idempotency.is_already_completed",
        lambda rid: check_calls.append(rid) or True
    )
    monkeypatch.setattr(executor, "_get_connection", lambda cid: {"type": "postgres"})
    monkeypatch.setattr(executor, "_create_engine", lambda c: MagicMock())

    try:
        executor.execute(
            df=pd.DataFrame({"x": [1]}),
            node_data={"connection_id": "c", "table_name": "t"},
            # NO run_id
        )
    except Exception:
        pass

    assert check_calls == [], (
        "Guard ha controllato idempotency anche senza run_id — deve essere no-op."
    )


# 6. Fail-open: eccezione nel check non blocca il flusso


def test_execute_proceeds_when_idempotency_check_raises(monkeypatch):
    """Se is_already_completed solleva (es. intermediate.db corrotto),
    NON bloccare la run — fail open al flow normale."""
    executor = CaricaExecutor(connection_service=MagicMock())

    def _boom(_run_id):
        raise RuntimeError("intermediate db corrupt")

    monkeypatch.setattr("app.core.intermediate.run_idempotency.is_already_completed", _boom)

    conn_calls = []
    monkeypatch.setattr(
        executor, "_get_connection",
        lambda cid: conn_calls.append(cid) or {"type": "postgres"}
    )
    monkeypatch.setattr(executor, "_create_engine", lambda c: MagicMock())

    try:
        executor.execute(
            df=pd.DataFrame({"x": [1]}),
            node_data={"connection_id": "c", "table_name": "t"},
            run_id="whatever",
        )
    except Exception:
        pass

    assert conn_calls, (
        "Guard ha bloccato il flusso su errore di is_already_completed — "
        "dovrebbe essere fail-open."
    )


# 7. Sentinel: import e chiamata corretti nell'executor


def test_execute_calls_is_already_completed_when_run_id_present():
    """Verifica che execute() chiami is_already_completed con run_id
    esattamente quando presente."""
    executor = CaricaExecutor(connection_service=MagicMock())

    calls = []

    def _spy(run_id):
        calls.append(run_id)
        return True  # forza short-circuit cosi' non dobbiamo mockare il resto

    with patch("app.core.intermediate.run_idempotency.is_already_completed", side_effect=_spy):
        executor.execute(
            df=pd.DataFrame({"x": [1]}),
            node_data={"connection_id": "c", "table_name": "t"},
            run_id="the-run",
        )

    assert calls == ["the-run"]


def test_get_connection_fallback_uses_carica_connection_store(monkeypatch):
    from app.core.carica import executor as executor_mod

    calls: list[str] = []
    expected = {
        "id": "conn-1",
        "name": "Dest",
        "type": "postgres",
        "host": "db",
        "port": 5432,
        "database": "main",
        "db_schema": "public",
        "username": "u",
        "password": "plain",
    }
    monkeypatch.setattr(
        executor_mod,
        "fetch_connection_config",
        lambda connection_id: calls.append(connection_id) or expected,
    )

    executor = CaricaExecutor()

    assert executor._get_connection("conn-1") == expected
    assert calls == ["conn-1"]


def test_connection_store_returns_legacy_shape_with_decrypted_password(monkeypatch):
    from app.core.carica import connection_store

    monkeypatch.setattr(
        connection_store,
        "fetchone",
        lambda sql, params: {
            "id": "conn-1",
            "name": "Dest",
            "db_type": "postgres",
            "host": "db",
            "port": 5432,
            "database": "main",
            "db_schema": "public",
            "username": "u",
            "password": "encrypted",
        },
    )
    monkeypatch.setattr(
        connection_store,
        "_decrypt_password",
        lambda value: f"decrypted:{value}",
    )

    connection = connection_store.fetch_connection_config("conn-1")

    assert connection == {
        "id": "conn-1",
        "name": "Dest",
        "type": "postgres",
        "host": "db",
        "port": 5432,
        "database": "main",
        "db_schema": "public",
        "username": "u",
        "password": "decrypted:encrypted",
    }


def test_carica_executor_has_no_direct_pg_connection_import():
    import inspect
    from pathlib import Path

    source_file = inspect.getsourcefile(CaricaExecutor)
    assert source_file is not None
    src = Path(source_file).read_text(encoding="utf-8")
    assert "app.db.pg_connection" not in src
