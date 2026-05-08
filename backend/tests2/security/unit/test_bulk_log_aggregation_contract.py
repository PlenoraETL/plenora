"""
Contract test — Batch 3.10.

Verifica che la telemetria strutturata emessa dai batch 3.1/3.2/3.4/3.5
"""

from __future__ import annotations

import json
import logging
from unittest.mock import MagicMock

import pytest

import app.db.pg_connection as _pg_conn_module
from app.utils.logger import (
    _BULK_TELEMETRY_EXTRA_WHITELIST,
    _BULK_TELEMETRY_LOGGERS,
    get_logger,
    set_request_id,
    set_user_id,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# Fixture


@pytest.fixture
def pg_execute_mock(monkeypatch):
    """Intercetta `app.db.pg_connection.execute` (import lazy dentro
    `PostgresLogHandler.emit`). Ogni INSERT in `logs` passa dal mock."""
    mock = MagicMock()
    monkeypatch.setattr(_pg_conn_module, "execute", mock)
    # Forza la configurazione del logger `app` (propagate=False + handler
    # attached) e del bulk telemetry handler (idempotent). Necessario
    # perche' il logger bulk `writer` e' child di `app`
    # e usano stdlib `logging.getLogger` direttamente.
    get_logger("app")
    # Reset dei ContextVar a stato pulito per ogni test (C8)
    set_request_id("")
    set_user_id("")
    yield mock


def _details_from_call(call) -> dict:
    """Estrae la colonna `details` JSONB dall'INSERT tuple del mock.
    Signature: `execute(sql, (ts, level, module, op, msg, project_id,
    group_id, run_id, node_id, details, error))` — posizione 9 della
    tupla params (0-indexed)."""
    params = call.args[1]
    details_json = params[9]
    return json.loads(details_json) if details_json else {}


def _level_from_call(call) -> str:
    return call.args[1][1]


def _emit_bulk_info(logger_name: str, msg: str, **extra):
    """Helper: emit INFO su un logger bulk con campi extra."""
    lg = logging.getLogger(logger_name)
    lg.info(msg, extra=extra)


def _emit_bulk_warning(logger_name: str, msg: str, **extra):
    lg = logging.getLogger(logger_name)
    lg.warning(msg, extra=extra)


# Sanity: whitelist e logger set costanti del modulo


def test_whitelist_contains_expected_keys():
    """La whitelist copre tutti i campi emessi dai batch 3.1/3.2/3.4/3.5."""
    expected = {
        "event", "reason", "dialect", "schema", "table",
        "rows", "error_type", "protocol", "path", "threshold",
    }
    assert set(_BULK_TELEMETRY_EXTRA_WHITELIST) == expected


def test_bulk_telemetry_logger_set_matches_runtime_emitters():
    """Il logger runtime noto emittente di telemetria bulk."""
    assert _BULK_TELEMETRY_LOGGERS == frozenset({
        "app.core.carica.writer",
    })


# Contract 1 — bulk.success INFO arriva al DB con whitelist serializzata


def test_bulk_info_extras_are_serialized_into_details_jsonb(pg_execute_mock):
    _emit_bulk_info(
        "app.core.carica.writer",
        "Bulk COPY completato: 500 righe",
        event="bulk.success",
        dialect="postgres",
        protocol="copy_from_stdin",
        schema="public",
        table="users",
        rows=500,
    )
    assert pg_execute_mock.call_count == 1
    details = _details_from_call(pg_execute_mock.call_args)
    assert details.get("event") == "bulk.success"
    assert details.get("dialect") == "postgres"
    assert details.get("protocol") == "copy_from_stdin"
    assert details.get("schema") == "public"
    assert details.get("table") == "users"
    assert details.get("rows") == 500
    assert _level_from_call(pg_execute_mock.call_args) == "INFO"


def test_bulk_info_mysql_success_includes_protocol_and_dialect(pg_execute_mock):
    """Contract storico del `PostgresLogHandler.emit` whitelist.

    In origine (3.2) verificava il payload emesso dal runtime
    `bulk_load_mysql`. Dopo la rimozione LOAD DATA (2026-04-23,
    kickoff.md §9.5) il runtime MySQL non esiste piu' — ma il
    contract del handler deve continuare a serializzare correttamente
    la whitelist se qualche altro consumer emettesse lo stesso payload
    (es. un retrofit futuro o un test). Test mantenuto come regression
    guard del log handler, non del path bulk MySQL."""
    _emit_bulk_info(
        "app.core.carica.writer",
        "Bulk LOAD DATA completato: 2000 righe",
        event="bulk.success",
        dialect="mysql",
        protocol="load_data_local_infile",
        schema="mydb",
        table="users",
        rows=2000,
    )
    details = _details_from_call(pg_execute_mock.call_args)
    assert details["event"] == "bulk.success"
    assert details["dialect"] == "mysql"
    assert details["protocol"] == "load_data_local_infile"


def test_stage_swap_dispatched_info_is_serialized(pg_execute_mock):
    """Simmetria `stage_swap.dispatched` (3.4) sul logger `writer`."""
    _emit_bulk_info(
        "app.core.carica.writer",
        "Stage+swap dispatch: dialect=mysql",
        event="stage_swap.dispatched",
        dialect="mysql",
        path="stage_swap",
        schema="mydb",
        table="orders",
        rows=750_000,
        threshold=500_000,
    )
    details = _details_from_call(pg_execute_mock.call_args)
    assert details["event"] == "stage_swap.dispatched"
    assert details["path"] == "stage_swap"
    assert details["dialect"] == "mysql"
    assert details["threshold"] == 500_000


# Contract 2 — bulk WARNING non duplicato fra handler WARNING e
# handler bulk-telemetry INFO (filter `levelno == INFO`)


def test_bulk_warning_is_not_duplicated(pg_execute_mock):
    _emit_bulk_warning(
        "app.core.carica.writer",
        "COPY fallito: BadCopyFileFormat",
        event="bulk.fallback",
        reason="copy_error",
        dialect="postgres",
        schema="public",
        table="t",
        rows=100,
        error_type="RuntimeError",
    )
    # 1 SOLO insert — filter INFO-only del bulk telemetry handler esclude
    # i WARNING; il WARNING handler default li raccoglie una volta.
    assert pg_execute_mock.call_count == 1
    details = _details_from_call(pg_execute_mock.call_args)
    assert details["event"] == "bulk.fallback"
    assert details["reason"] == "copy_error"
    assert details["error_type"] == "RuntimeError"
    assert _level_from_call(pg_execute_mock.call_args) == "WARNING"


# Contract 3 — INFO su logger non-bulk NON arriva al DB


def test_non_bulk_info_does_not_reach_db(pg_execute_mock):
    """Il filter del bulk telemetry handler rifiuta record da logger
    diversi dai 2 bulk. Il WARNING handler default filtra l'INFO per
    livello. Risultato: zero INSERT."""
    lg = logging.getLogger("app.core.estrai.query_executor")
    lg.info("preview esteso", extra={"event": "estrai.preview", "rows": 10})
    assert pg_execute_mock.call_count == 0


def test_non_bulk_warning_still_reaches_db_via_default_handler(pg_execute_mock):
    """I WARNING su qualsiasi logger `app.*` continuano ad arrivare al
    DB come prima di 3.10 (handler default). 3.10 non rompe questo
    flusso."""
    lg = logging.getLogger("app.core.estrai.query_executor")
    lg.warning("query lenta > 30s")
    assert pg_execute_mock.call_count == 1
    # Il record non ha campi whitelist, `details` non deve contenerli
    details = _details_from_call(pg_execute_mock.call_args)
    for key in _BULK_TELEMETRY_EXTRA_WHITELIST:
        assert key not in details, f"chiave whitelist '{key}' spuria in details"


# Contract 4 — solo la whitelist finisce in details; altre chiavi NO


def test_non_whitelist_extras_are_not_serialized(pg_execute_mock):
    _emit_bulk_info(
        "app.core.carica.writer",
        "test",
        event="bulk.success",
        dialect="postgres",
        malicious_injection="<script>",
        sensitive_token="bearer-xxx",
        random_noise=42,
    )
    details = _details_from_call(pg_execute_mock.call_args)
    assert details["event"] == "bulk.success"
    assert details["dialect"] == "postgres"
    assert "malicious_injection" not in details
    assert "sensitive_token" not in details
    assert "random_noise" not in details


# Contract 5 — ContextVar injection (C8) preservata e coesistente


def test_bulk_info_preserves_contextvar_injection(pg_execute_mock):
    set_request_id("req-3dot10-test")
    set_user_id("user-3dot10-test")
    try:
        _emit_bulk_info(
            "app.core.carica.writer",
            "Bulk COPY completato",
            event="bulk.success",
            dialect="postgres",
            protocol="copy_from_stdin",
            schema="public",
            table="t",
            rows=100,
        )
        details = _details_from_call(pg_execute_mock.call_args)
        # Whitelist 3.10
        assert details["event"] == "bulk.success"
        assert details["protocol"] == "copy_from_stdin"
        # ContextVar C8 preservato
        assert details["request_id"] == "req-3dot10-test"
        assert details["user_id"] == "user-3dot10-test"
    finally:
        set_request_id("")
        set_user_id("")


# Contract 6 — shape JSONB query-abile via `->>` SQL operator


def test_details_jsonb_supports_top_level_key_extraction(pg_execute_mock):
    """Il valore di `details` deve essere JSON parseabile con chiavi
    top-level accessibili (no doppio-nesting). Questo e' cio' che abilita
    `SELECT COUNT(*) FROM logs WHERE details->>'event' = 'bulk.success'`
    in SQL PG."""
    _emit_bulk_info(
        "app.core.carica.writer",
        "t",
        event="bulk.success",
        dialect="postgres",
        rows=1,
    )
    raw_details = pg_execute_mock.call_args.args[1][9]
    # Deve essere JSON string valida
    parsed = json.loads(raw_details)
    # Chiavi top-level dirette (no wrapping)
    assert parsed["event"] == "bulk.success"
    assert parsed["dialect"] == "postgres"
    assert parsed["rows"] == 1
