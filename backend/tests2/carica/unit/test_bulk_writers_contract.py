"""Contract test — `app.core.carica.bulk_writers`.

Copertura post-rimozione dei path bulk (2026-04-23):
  - feature flag `CARICA_BULK_INSERT` / `is_bulk_enabled()`
  - dispatcher `bulk_insert` — raise `BulkInsertNotSupportedError
    (reason="dialect_not_supported")` per tutti i dialect post-rimozione
  - telemetria strutturata `event=bulk.fallback` con correlation IDs
    (`request_id`, `user_id`) su ogni invocazione del dispatcher
  - tombstone anti-regressione: PG e MySQL non devono tornare nel
    dispatcher (`test_dispatcher_*_tombstoned_*`)

Storico coperture rimosse con i path:
  - MySQL LOAD DATA (kickoff §9.5, commit 54d6e44): 11 test PG-specific
    rimossi in quel batch
  - Postgres COPY (kickoff §9.6, questo commit): ~28 test PG-specific
    rimossi (roundtrip types, happy path, quoting, NULL serialization,
    escape, column order, missing col, db error fallback, no DBAPI,
    geometry raise, empty df, all skipped, dispatch aliases, telemetry
    fallback PG, success PG, correlation PG). Helpers correlati
    (`_has_geometry`, `_ordered_*`, `_quote_pg_ident`,
    `_df_subset_for_bulk`) rimossi in runtime.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from app.core.carica.bulk_writers import (
    BulkInsertNotSupportedError,
    bulk_insert,
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
    """Mock SQLAlchemy connection con DBAPI cursor accessibile.
    Post-rimozione bulk, il dispatcher raise prima di usare sa_conn; il
    mock e' preservato per eventuali test futuri di scaffolding."""
    sa_conn = MagicMock()
    cursor = MagicMock()
    sa_conn.connection.cursor.return_value = cursor
    return sa_conn, cursor


# Feature flag — `CARICA_BULK_INSERT` / `is_bulk_enabled()`


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


# Dispatch — post-rimozione raise sempre `dialect_not_supported`


@pytest.mark.parametrize(
    "dialect",
    [
        # Dialect mai avuti bulk dedicato
        "oracle", "mssql", "sqlite", "",
        # MySQL tombstoned 2026-04-23 (§9.5)
        "mysql", "mariadb", "MySQL", "MariaDB",
        # Postgres tombstoned 2026-04-23 (§9.6)
        "postgres", "postgresql", "POSTGRES", "PostgreSQL",
    ],
)
def test_dispatch_unsupported_dialect(dialect):
    """Post-rimozione bulk native paths (2026-04-23): qualsiasi dialect
    passato al dispatcher raise `BulkInsertNotSupportedError(reason=
    "dialect_not_supported")`. Il caller `_execute_batch` fa fallback
    automatico ad executemany.

    Tombstone guard:
    - Se qualcuno riaggiunge la branch `if dialect in ("postgres", ...)`
      o `if dialect in ("mysql", ...)` al dispatcher, questo test
      smette di passare per quel dialect.
    """
    sa_conn, _ = _mock_sa_conn()
    import pandas as pd
    df = pd.DataFrame({"x": [1]})
    cols = [_col("x")]
    with pytest.raises(BulkInsertNotSupportedError, match="non implementato"):
        bulk_insert(sa_conn, dialect, "s", "t", cols, df)


# Telemetria dispatcher — correlation IDs + tombstone


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


@pytest.fixture
def with_correlation_ids():
    """Setta request_id + user_id nei ContextVar e fa reset a fine test."""
    from app.utils.logger import set_request_id, set_user_id
    set_request_id("req-dispatcher-test")
    set_user_id("user-dispatcher-test")
    try:
        yield ("req-dispatcher-test", "user-dispatcher-test")
    finally:
        set_request_id("")
        set_user_id("")


def test_dispatcher_unsupported_dialect_emits_info_log(bulk_caplog):
    """Dispatcher telemetry generica: un dialect qualunque non supportato
    (es. oracle, che non ha mai avuto bulk dedicato) produce
    `bulk.fallback reason=dialect_not_supported` INFO."""
    sa_conn, _ = _mock_sa_conn()
    import pandas as pd
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


def test_dispatcher_mysql_tombstoned_emits_dialect_not_supported(bulk_caplog):
    """Post-rimozione LOAD DATA (2026-04-23, kickoff §9.5): MySQL cade
    nel raise `dialect_not_supported`. Guard contro re-introduzione
    silenziosa della branch mysql/mariadb nel dispatcher."""
    sa_conn, _ = _mock_sa_conn()
    import pandas as pd
    df = pd.DataFrame({"id": [1, 2, 3]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="non implementato"):
        bulk_insert(sa_conn, "mysql", "mydb", "users", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.reason == "dialect_not_supported"
    assert r.dialect == "mysql"
    assert r.schema == "mydb"
    assert r.table == "users"
    assert r.rows == 3


def test_dispatcher_postgres_tombstoned_emits_dialect_not_supported(
    bulk_caplog, with_correlation_ids,
):
    """Post-rimozione COPY FROM STDIN (2026-04-23, kickoff §9.6): anche
    Postgres cade nel raise `dialect_not_supported`. Guard contro
    re-introduzione silenziosa della branch postgres/postgresql nel
    dispatcher.

    Questo test verifica ANCHE il passaggio dei correlation IDs
    (`request_id`, `user_id`) dai ContextVar al payload log — unica
    verifica rimasta post-rimozione dei test correlation PG/MySQL
    success specifici."""
    expected_req, expected_user = with_correlation_ids
    sa_conn, _ = _mock_sa_conn()
    import pandas as pd
    df = pd.DataFrame({"id": [1, 2, 3, 4, 5]})
    cols = [_col("id")]
    with pytest.raises(BulkInsertNotSupportedError, match="non implementato"):
        bulk_insert(sa_conn, "postgres", "public", "users", cols, df)
    recs = _bulk_fallback_records(bulk_caplog)
    assert len(recs) == 1
    r = recs[0]
    assert r.levelname == "INFO"
    assert r.reason == "dialect_not_supported"
    assert r.dialect == "postgres"
    assert r.schema == "public"
    assert r.table == "users"
    assert r.rows == 5
    # Correlation IDs propagati dai ContextVar (ex 3.4)
    assert r.request_id == expected_req
    assert r.user_id == expected_user
