"""Contract test — `app.core.carica.bulk_writers`.

Coperture:
  - dispatch per dialect (postgres → COPY, mysql → LOAD DATA,
    altri → BulkInsertNotSupportedError)
  - feature flag CARICA_BULK_INSERT (auto/on/off)
  - geometry → BulkInsertNotSupportedError (no fallback automatico:
    il caller deve gestire)
  - empty df → 0 (early return)
  - identifier quoting: schema/table/colonne con caratteri patologici
  - CSV serialization: NULL handling, escape virgole/quote/newline
  - Postgres: copy_expert chiamato con SQL ben formato
  - MySQL: error 1148 → BulkInsertNotSupportedError esplicito
  - Postgres: BadCopyFileFormat → BulkInsertNotSupportedError (fallback)
"""

from __future__ import annotations

import csv
import io
import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.bulk_writers import (
    BulkInsertNotSupportedError,
    bulk_copy_postgres,
    bulk_insert,
    bulk_load_mysql,
    is_bulk_enabled,
)
from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _col(src: str, dst: str = None, **kwargs) -> ColumnMapping:
    return ColumnMapping(
        source_column=src,
        dest_column=dst or src,
        **kwargs,
    )


def _mock_sa_conn():
    """Crea un mock SQLAlchemy connection con DBAPI cursor accessibile."""
    sa_conn = MagicMock()
    cursor = MagicMock()
    sa_conn.connection.cursor.return_value = cursor
    return sa_conn, cursor


# =============================================================================
# Feature flag
# =============================================================================


def test_is_bulk_enabled_default_auto(monkeypatch):
    monkeypatch.delenv("CARICA_BULK_INSERT", raising=False)
    assert is_bulk_enabled() is True


@pytest.mark.parametrize("val", ["on", "true", "1", "auto", "yes", "ON"])
def test_is_bulk_enabled_truthy(monkeypatch, val):
    monkeypatch.setenv("CARICA_BULK_INSERT", val)
    assert is_bulk_enabled() is True


@pytest.mark.parametrize("val", ["off", "false", "0", "no", "disabled"])
def test_is_bulk_enabled_disabled(monkeypatch, val):
    monkeypatch.setenv("CARICA_BULK_INSERT", val)
    assert is_bulk_enabled() is False


# =============================================================================
# Geometry fallback
# =============================================================================


def test_postgres_with_geometry_raises_not_supported():
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})
    cols = [_col("id"), _col("geom", is_geometry=True)]
    with pytest.raises(BulkInsertNotSupportedError, match="geometry"):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)


def test_mysql_with_geometry_raises_not_supported():
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})
    cols = [_col("id"), _col("geom", is_geometry=True)]
    with pytest.raises(BulkInsertNotSupportedError, match="geometry"):
        bulk_load_mysql(sa_conn, "myschema", "t", cols, df)


# =============================================================================
# Empty / no active columns
# =============================================================================


def test_empty_df_returns_zero():
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": []})
    cols = [_col("id")]
    assert bulk_copy_postgres(sa_conn, "public", "t", cols, df) == 0
    cursor.copy_expert.assert_not_called()


def test_all_columns_skipped_raises():
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1]})
    cols = [_col("id", skip=True)]
    with pytest.raises(BulkInsertNotSupportedError, match="Nessuna colonna"):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)


# =============================================================================
# Postgres COPY happy path
# =============================================================================


def test_postgres_copy_happy_path():
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    cols = [_col("id"), _col("name")]

    n = bulk_copy_postgres(sa_conn, "public", "users", cols, df)

    assert n == 3
    cursor.copy_expert.assert_called_once()
    sql, buf = cursor.copy_expert.call_args.args
    # SQL ben formato
    assert "COPY" in sql
    assert '"public"."users"' in sql
    assert '"id"' in sql and '"name"' in sql
    assert "FORMAT CSV" in sql
    # Buffer contiene i dati CSV
    csv_content = buf.read() if hasattr(buf, "read") else buf
    assert "1,a" in csv_content
    assert "3,c" in csv_content


def test_postgres_copy_no_schema():
    """Schema vuoto/None → nessun prefisso schema."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    bulk_copy_postgres(sa_conn, "", "tbl", cols, df)
    sql = cursor.copy_expert.call_args.args[0]
    assert '"tbl"' in sql
    assert ".\"tbl\"" not in sql  # niente schema-qualifier


def test_postgres_copy_quotes_dangerous_identifiers():
    """Nomi tabelle/colonne con doppi apici devono essere quotati safely."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"col\"with\"quote": [1]})
    cols = [_col("col\"with\"quote")]
    bulk_copy_postgres(sa_conn, "sch\"ma", "ta\"ble", cols, df)
    sql = cursor.copy_expert.call_args.args[0]
    # Doppi apici raddoppiati
    assert '"sch""ma"' in sql
    assert '"ta""ble"' in sql
    assert '"col""with""quote"' in sql


def test_postgres_copy_null_serialization():
    """pandas NaN → stringa vuota CSV → riconosciuta come NULL ('NULL '')."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2], "name": ["a", None]})
    cols = [_col("id"), _col("name")]
    bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    _sql, buf = cursor.copy_expert.call_args.args
    csv_content = buf.read()
    # Riga 2: "2,\n" (name vuoto)
    lines = csv_content.strip().split("\n")
    assert lines[1] == "2,"
    # SQL specifica NULL ''
    assert "NULL ''" in _sql


def test_postgres_copy_escapes_special_chars():
    """Virgole, quote e newline nei valori vengono quotati da CSV writer."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({
        "txt": ['a,b', 'with "quote"', 'multi\nline'],
    })
    cols = [_col("txt")]
    bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    csv_content = cursor.copy_expert.call_args.args[1].read()
    # I valori devono essere tutti enclosed in quotes per via dei chars speciali
    assert '"a,b"' in csv_content
    assert '"with ""quote"""' in csv_content


def test_postgres_copy_column_order_matches_mappings():
    """L'ordine delle colonne nel CSV deve seguire le mappature, non
    il DataFrame originale."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    # Ordine esplicito: c, a (skip b)
    cols = [_col("c"), _col("a")]
    bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    sql = cursor.copy_expert.call_args.args[0]
    # Controlliamo la sequenza nel COPY ... (col1, col2)
    cols_part = sql.split("(", 1)[1].split(")", 1)[0]
    assert cols_part == '"c", "a"'
    # Anche il CSV ha c prima di a
    csv_content = cursor.copy_expert.call_args.args[1].read()
    assert csv_content.strip() == "3,1"


def test_postgres_copy_missing_source_column_raises_value_error():
    """Mappatura punta a colonna mancante → ValueError (errore programmatico,
    non BulkInsertNotSupportedError che farebbe fallback inutile)."""
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"a": [1]})
    cols = [_col("missing_col")]
    with pytest.raises(ValueError, match="manca colonne"):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)


def test_postgres_copy_db_error_falls_back():
    """Errore DB durante copy_expert → BulkInsertNotSupportedError per
    permettere al caller il fallback executemany (che dara' errore
    per-riga interpretabile)."""
    sa_conn, cursor = _mock_sa_conn()
    cursor.copy_expert.side_effect = RuntimeError("BadCopyFileFormat: line 3")
    df = pd.DataFrame({"id": [1, 2, 3]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="BadCopyFileFormat"):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)


def test_postgres_copy_no_dbapi_cursor_falls_back():
    """SA connection senza .connection.cursor → fallback."""
    sa_conn = MagicMock()
    # Rimuovi attributo cursor dal raw_conn
    raw = MagicMock(spec=[])  # no attributes
    sa_conn.connection = raw
    df = pd.DataFrame({"id": [1]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="DBAPI cursor"):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)


# =============================================================================
# MySQL LOAD DATA
# =============================================================================


def test_mysql_load_happy_path():
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    cols = [_col("id"), _col("name")]

    n = bulk_load_mysql(sa_conn, "mydb", "users", cols, df)

    assert n == 2
    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert "LOAD DATA LOCAL INFILE" in sql
    assert "`mydb`.`users`" in sql
    assert "`id`, `name`" in sql
    assert "CHARACTER SET utf8mb4" in sql


def test_mysql_load_local_infile_disabled_raises_not_supported():
    """Error 1148 → BulkInsertNotSupportedError esplicito (caller fallback)."""
    sa_conn, cursor = _mock_sa_conn()
    cursor.execute.side_effect = RuntimeError(
        "(1148, 'The used command is not allowed with this MySQL version')"
    )
    df = pd.DataFrame({"id": [1]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="LOCAL INFILE disabilitato"):
        bulk_load_mysql(sa_conn, "db", "t", cols, df)


def test_mysql_load_generic_db_error_falls_back():
    sa_conn, cursor = _mock_sa_conn()
    cursor.execute.side_effect = RuntimeError("Some other DB error")
    df = pd.DataFrame({"id": [1]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="Some other DB error"):
        bulk_load_mysql(sa_conn, "db", "t", cols, df)


def test_mysql_load_quotes_dangerous_identifiers():
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x", "weird`col")]
    bulk_load_mysql(sa_conn, "db`name", "tab`le", cols, df)
    sql = cursor.execute.call_args.args[0]
    # Backtick raddoppiati
    assert "`db``name`.`tab``le`" in sql
    assert "`weird``col`" in sql


def test_mysql_load_no_schema():
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    bulk_load_mysql(sa_conn, "", "t", cols, df)
    sql = cursor.execute.call_args.args[0]
    assert "INTO TABLE `t`" in sql


# =============================================================================
# Dispatch
# =============================================================================


@pytest.mark.parametrize("dialect", ["postgres", "postgresql", "POSTGRES", "PostgreSQL"])
def test_dispatch_postgres_aliases(dialect):
    """Sia `postgres` che `postgresql` (alias SQLAlchemy ufficiale)
    devono andare al COPY path, case-insensitive."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    bulk_insert(sa_conn, dialect, "public", "t", cols, df)
    cursor.copy_expert.assert_called_once()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "MySQL", "MariaDB"])
def test_dispatch_mysql_and_mariadb(dialect):
    """MariaDB usa lo stesso protocollo wire di MySQL (driver pymysql/
    mysqlclient comuni) → stesso path LOAD DATA."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    bulk_insert(sa_conn, dialect, "db", "t", cols, df)
    cursor.execute.assert_called_once()
    sql = cursor.execute.call_args.args[0]
    assert "LOAD DATA LOCAL INFILE" in sql


@pytest.mark.parametrize("dialect", ["oracle", "mssql", "sqlite", ""])
def test_dispatch_unsupported_dialect(dialect):
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    with pytest.raises(BulkInsertNotSupportedError, match="non implementato"):
        bulk_insert(sa_conn, dialect, "s", "t", cols, df)


# =============================================================================
# Fallback telemetry — event="bulk.fallback" structured log (Batch 3.1)
# =============================================================================
#
# Ogni attivazione del fallback (BulkInsertNotSupportedError) emette un log
# strutturato con payload fisso: event, reason, dialect, schema, table, rows,
# error_type. Livelli: INFO per fallback by-design (geometry, driver
# unsupported, dialect non implementato), WARNING per fallback operativi
# (cursor failure, server disable LOCAL INFILE, COPY/LOAD DATA error).


@pytest.fixture
def bulk_caplog(caplog):
    """Propaga i record di `app.core.carica.bulk_writers` al caplog handler.

    Il logger `app` del progetto ha `propagate=False` (vedi
    `app.utils.logger.get_logger`), quindi caplog (handler attaccato al
    root) non riceverebbe nulla. Agganciamo temporaneamente l'handler di
    caplog direttamente al logger target, senza alterare propagate."""
    target = logging.getLogger("app.core.carica.bulk_writers")
    target.addHandler(caplog.handler)
    orig_level = target.level
    target.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        target.removeHandler(caplog.handler)
        target.setLevel(orig_level)


def _bulk_fallback_records(caplog):
    return [
        r for r in caplog.records
        if getattr(r, "event", None) == "bulk.fallback"
    ]


def test_pg_geometry_emits_structured_fallback_log(bulk_caplog):
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})
    cols = [_col("id"), _col("geom", is_geometry=True)]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.reason == "geometry"
    assert r.dialect == "postgres"
    assert r.schema == "public"
    assert r.table == "t"
    assert r.rows == 1
    assert r.error_type is None


def test_pg_copy_error_emits_structured_warning(bulk_caplog):
    sa_conn, cursor = _mock_sa_conn()
    cursor.copy_expert.side_effect = RuntimeError("BadCopyFileFormat: line 3")
    df = pd.DataFrame({"id": [1, 2, 3]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "WARNING"
    assert r.reason == "copy_error"
    assert r.dialect == "postgres"
    assert r.schema == "public"
    assert r.table == "t"
    assert r.rows == 3
    assert r.error_type == "RuntimeError"


def test_mysql_local_infile_disabled_emits_structured_warning(bulk_caplog):
    sa_conn, cursor = _mock_sa_conn()
    cursor.execute.side_effect = RuntimeError(
        "(1148, 'The used command is not allowed with this MySQL version')"
    )
    df = pd.DataFrame({"id": [1]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_load_mysql(sa_conn, "db", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "WARNING"
    assert r.reason == "server_disable_local_infile"
    assert r.dialect == "mysql"
    assert r.schema == "db"
    assert r.table == "t"
    assert r.rows == 1
    assert r.error_type == "RuntimeError"


def test_mysql_geometry_emits_structured_info(bulk_caplog):
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})
    cols = [_col("id"), _col("geom", is_geometry=True)]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_load_mysql(sa_conn, "myschema", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.reason == "geometry"
    assert r.dialect == "mysql"
    assert r.schema == "myschema"
    assert r.table == "t"
    assert r.rows == 1
    assert r.error_type is None


def test_dispatcher_unsupported_dialect_emits_info_log(bulk_caplog):
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_insert(sa_conn, "oracle", "s", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.reason == "dialect_not_supported"
    assert r.dialect == "oracle"
    assert r.schema == "s"
    assert r.table == "t"
    assert r.rows == 2
    assert r.error_type is None


# =============================================================================
# Success telemetry — event="bulk.success" (Batch 3.2)
# =============================================================================
#
# Counter simmetrico a bulk.fallback, emesso SOLO sul ramo di successo di
# bulk_load_mysql. Abilita §6.2.4 della performance_bulk_review: calcolo di
# success_rate = #bulk.success / (#bulk.success + #bulk.fallback[MySQL])
# post-deploy 2-4 settimane. Asimmetria PG→MySQL voluta: PG non e'
# candidato a deprecazione, non richiede success_rate.


def _bulk_success_records(caplog):
    return [
        r for r in caplog.records
        if getattr(r, "event", None) == "bulk.success"
    ]


def test_mysql_success_emits_structured_info_log(bulk_caplog):
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    cols = [_col("id"), _col("name")]
    n = bulk_load_mysql(sa_conn, "mydb", "users", cols, df)
    assert n == 3
    cursor.execute.assert_called_once()  # happy path: nessuna eccezione
    recs = _bulk_success_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.dialect == "mysql"
    assert r.schema == "mydb"
    assert r.table == "users"
    assert r.rows == 3
    assert r.protocol == "load_data_local_infile"
    # Asserzione negativa: nessun bulk.fallback su success path
    assert _bulk_fallback_records(bulk_caplog) == []


def test_pg_success_emits_copy_from_stdin_protocol_log(bulk_caplog):
    """Batch 3.5: chiude la simmetria osservativa del punto 3.

    In 3.2 scelsimo deliberatamente di non strumentare il PG success path
    perche' lo scope era MySQL-only e PG non era candidato a deprecazione
    (review §3.1, §4 A1). In 3.5 estendiamo la telemetria success anche a
    PG per abilitare il calcolo di success_rate_pg (criterio "keep
    confermato" che verra' formalizzato in 3.6 / kickoff.md §9.3).
    Il protocol="copy_from_stdin" distingue PG da MySQL
    ("load_data_local_infile") senza richiedere parse di dialect."""
    sa_conn, cursor = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    cols = [_col("id"), _col("name")]
    n = bulk_copy_postgres(sa_conn, "public", "users", cols, df)
    assert n == 2
    cursor.copy_expert.assert_called_once()  # happy path PG
    recs = _bulk_success_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.dialect == "postgres"
    assert r.protocol == "copy_from_stdin"
    assert r.schema == "public"
    assert r.table == "users"
    assert r.rows == 2
    # Asserzione negativa: nessun bulk.fallback su success path
    assert _bulk_fallback_records(bulk_caplog) == []


# =============================================================================
# Correlation IDs nei log (Batch 3.4) — chiude il gap di stratificazione
# =============================================================================
#
# Ogni record bulk.* porta request_id + user_id letti da ContextVar
# (app.utils.logger). In Celery worker user_id e' stringa vuota (non
# propagato), request_id e' il task_id via celery_app.py:146-157.
# Stratificazione per user in §9.2 si ottiene via join offline su
# log_runs.run_id = bulk_log.request_id.


@pytest.fixture
def with_correlation_ids():
    """Setta request_id + user_id nei ContextVar e fa reset a fine test."""
    from app.utils.logger import set_request_id, set_user_id
    set_request_id("req-3dot4-test")
    set_user_id("user-3dot4-test")
    try:
        yield ("req-3dot4-test", "user-3dot4-test")
    finally:
        set_request_id("")
        set_user_id("")


def test_bulk_fallback_includes_correlation_ids(bulk_caplog, with_correlation_ids):
    """Un fallback tipico (PG geometry, scelto come caso minimo) deve
    portare request_id e user_id letti dai ContextVar."""
    expected_req, expected_user = with_correlation_ids
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1], "geom": ["POINT(0 0)"]})
    cols = [_col("id"), _col("geom", is_geometry=True)]
    with pytest.raises(BulkInsertNotSupportedError):
        bulk_copy_postgres(sa_conn, "public", "t", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.request_id == expected_req
    assert r.user_id == expected_user


def test_bulk_success_mysql_includes_correlation_ids(bulk_caplog, with_correlation_ids):
    """Happy path MySQL deve portare request_id e user_id. Questo abilita
    il join post-deploy su log_runs per ottenere tenant/user stratification."""
    expected_req, expected_user = with_correlation_ids
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    cols = [_col("id"), _col("name")]
    bulk_load_mysql(sa_conn, "mydb", "users", cols, df)
    recs = _bulk_success_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.request_id == expected_req
    assert r.user_id == expected_user


def test_bulk_success_pg_includes_correlation_ids(bulk_caplog, with_correlation_ids):
    """Simmetrico al test MySQL (3.4): PG COPY success (3.5) deve portare
    request_id e user_id dai ContextVar. Abilita success_rate_pg
    stratificato per user via join offline su log_runs."""
    expected_req, expected_user = with_correlation_ids
    sa_conn, _ = _mock_sa_conn()
    df = pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]})
    cols = [_col("id"), _col("name")]
    bulk_copy_postgres(sa_conn, "public", "users", cols, df)
    recs = _bulk_success_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.dialect == "postgres"
    assert r.protocol == "copy_from_stdin"
    assert r.request_id == expected_req
    assert r.user_id == expected_user
