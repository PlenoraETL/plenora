"""
Reconciliation E2E smoke — PG source -> DatabaseWriter -> PG dest.

T-08 / D2 typed context closure: chiude il gap F-DATA-09 dell'audit data ("nessun
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys

import pandas as pd
import pytest
from sqlalchemy import create_engine

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping,
    TransactionMode,
    WriteConfig,
    WriteMode,
    WriteStatus,
)
from tests2.integration.harness.db import make_it_id
from tests2.integration.harness.postgres_ext import PG_EXT_DATABASE, PG_EXT_HOST, PG_EXT_PASSWORD, PG_EXT_PORT, PG_EXT_USER, postgres_ext_sqlalchemy_url
from tests2.integration.harness.reconciliation import (
    assert_reconciliation_match,
    compute_checksums,
    create_source_table,
    drop_table_if_exists,
    gen_deterministic_dataset,
    insert_rows_bulk,
)


pytestmark = [
    pytest.mark.integration,
    pytest.mark.contract,
    pytest.mark.regression,
    pytest.mark.requires_postgres,
    pytest.mark.developer_nightly,
    pytest.mark.db_live_opt_in,
]


# Skip policy: env opt-in + probe-connect, no `psycopg2.connect` se mancano.

_RUN_DB_LIVE = os.getenv("URBY_RUN_DB_LIVE_TESTS") == "1"

_SKIP_NO_DB_LIVE_OPT_IN = pytest.mark.skipif(
    not _RUN_DB_LIVE,
    reason=(
        "DB-live opt-in mancante: settare URBY_RUN_DB_LIVE_TESTS=1 "
        "per eseguire il smoke reconciliation su PG ext "
        f"({PG_EXT_HOST}:{PG_EXT_PORT}/{PG_EXT_DATABASE}). Skip default per evitare "
        "access violation su psycopg2 in ambienti senza PG ext "
        "raggiungibile."
    ),
)


# Preflight TCP + subprocess psycopg2.connect
#
# Pattern imparato in Batch B review #1-#5: su Windows `psycopg2` puo'
# produrre Windows fatal access violation lato libpq prima di sollevare
# OperationalError. Un try/except Python nel processo pytest NON
# intercetta i crash nativi. L'unica difesa solida e':
#   1. controllare la raggiungibilita' TCP della porta con `socket`
#      puro (zero codice C esterno);
#   2. se TCP ok, eseguire `psycopg2.connect` in un SOTTOPROCESSO
#      isolato. Se il sottoprocesso muore con access violation, il
#      processo pytest sopravvive e fa `pytest.skip(...)`.
#
# Solo dopo i due preflight il test usa `postgres_ext_conn()` nel
# processo principale: a quel punto sappiamo che TCP risponde e che
# psycopg2.connect ha completato in un altro processo, quindi e'
# ragionevolmente safe nel processo pytest.

# Snippet eseguito in subprocess: solo `psycopg2.connect` con
# `connect_timeout`. Stampa "OK" stdout su success, exit code != 0 o
# crash nativo => skip nel parent.
_SUBPROCESS_PROBE_CODE = (
    "import psycopg2,sys;"
    "psycopg2.connect("
    f"host={PG_EXT_HOST!r},port={PG_EXT_PORT},"
    f"dbname={PG_EXT_DATABASE!r},"
    f"user={PG_EXT_USER!r},password={PG_EXT_PASSWORD!r},"
    "connect_timeout=2).close();"
    "sys.stdout.write('OK')"
)


def _tcp_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    """Preflight TCP-only: zero psycopg2, zero codice C esterno."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _subprocess_psycopg2_probe(timeout: float = 5.0) -> tuple[bool, str]:
    """Esegue psycopg2.connect in subprocess isolato.

    Ritorna (ok, diagnostic). Se il subprocess crasha (access violation)
    o termina con exit code != 0, ok=False.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_PROBE_CODE],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "subprocess probe timeout"
    except OSError as exc:
        return False, f"subprocess spawn failed: {exc}"

    if result.returncode != 0:
        diag = (result.stderr or result.stdout or "").strip().splitlines()
        last_line = diag[-1] if diag else f"exit code {result.returncode}"
        return False, f"psycopg2 probe failed: {last_line}"
    if "OK" not in (result.stdout or ""):
        return False, "psycopg2 probe non ha stampato OK"
    return True, "OK"


@pytest.fixture
def pg_ext_reachable():
    """Skip pulito se PG ext non raggiungibile, senza chiamare psycopg2
    nel processo pytest finche' il subprocess probe non ha confermato
    che il connect non crasha lato C.

    Quattro layer di difesa (in ordine di esecuzione):
      1. richeck env opt-in (ridondanza con `_SKIP_NO_DB_LIVE_OPT_IN`)
      2. preflight TCP via `socket.create_connection`
      3. preflight psycopg2 in subprocess isolato
      4. solo dopo, `postgres_ext_conn()` puo' essere usato nel test
    """
    if not _RUN_DB_LIVE:
        pytest.skip("DB-live test non opt-in (URBY_RUN_DB_LIVE_TESTS=1)")

    # Layer 1: TCP-only, niente driver C
    if not _tcp_reachable(PG_EXT_HOST, PG_EXT_PORT):
        pytest.skip(
            f"PG ext TCP non raggiungibile su "
            f"{PG_EXT_HOST}:{PG_EXT_PORT} (timeout 2s)"
        )

    # Layer 2: psycopg2 in subprocess isolato. Se libpq crasha
    # nativamente (access violation Windows), il subprocess muore ma
    # il processo pytest sopravvive.
    ok, diag = _subprocess_psycopg2_probe()
    if not ok:
        pytest.skip(
            f"PG ext non raggiungibile via psycopg2 (subprocess probe): {diag}"
        )


# Engine + WriteConfig builders

def _pg_ext_engine():
    """SQLAlchemy engine verso lo stesso PG ext del harness."""
    return create_engine(postgres_ext_sqlalchemy_url())


def _make_write_config(dest_table: str) -> WriteConfig:
    """WriteConfig minimale per CREATE + ALL_OR_NOTHING su tabella nuova.

    Tipi destinazione allineati alla source:
      - id      INTEGER PRIMARY KEY
      - name    TEXT
      - amount  NUMERIC(12,2)
      - payload TEXT
    """
    return WriteConfig(
        connection_id="pg-ext-recon-smoke",  # logical id, non usato dal writer
        schema_name="public",
        table_name=dest_table,
        write_mode=WriteMode.CREATE,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        if_table_exists="error",
        batch_size=1000,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="id",
                          dest_type="INTEGER", is_primary_key=True),
            ColumnMapping(source_column="name", dest_column="name",
                          dest_type="TEXT"),
            ColumnMapping(source_column="amount", dest_column="amount",
                          dest_type="NUMERIC(12,2)"),
            ColumnMapping(source_column="payload", dest_column="payload",
                          dest_type="TEXT"),
        ],
    )


# Test: smoke reconciliation 10k righe

@_SKIP_NO_DB_LIVE_OPT_IN
@pytest.mark.usefixtures("pg_ext_reachable")
def test_pg_reconciliation_smoke_10k_rows():
    """Source PG -> DatabaseWriter -> Dest PG. Reconciliation 3-metriche.

    Volume: 10k righe (sopra `batch_size=1000`, esercita il bulk path
    multi-batch del writer; sotto soglia trivial).
    Atteso: count, sum, md5 identici src/dest. Tempo: ~10-30s su PG locale.
    """
    src_table = make_it_id("recon_src")
    dst_table = make_it_id("recon_dst")
    rows = gen_deterministic_dataset(n=10_000, seed=42)
    engine = _pg_ext_engine()

    try:
        # 1. Seed source
        create_source_table(src_table)
        insert_rows_bulk(src_table, rows)

        # 2. Carica via DatabaseWriter (CREATE su dest_table vergine)
        df = pd.DataFrame(rows)
        config = _make_write_config(dst_table)
        writer = DatabaseWriter(config=config, engine=engine, db_type="postgres")
        result = writer.write(df)

        assert result.status == WriteStatus.SUCCESS, (
            f"Writer status atteso SUCCESS, trovato {result.status}. "
            f"Eventuale error: {getattr(result, 'error_message', None)}"
        )
        assert result.rows_written == len(rows), (
            f"Writer rows_written={result.rows_written} vs atteso={len(rows)}"
        )

        # 3. Checksum simmetrici src vs dest
        src_checksums = compute_checksums(schema="public", table=src_table)
        dst_checksums = compute_checksums(schema="public", table=dst_table)

        # Sanity: source checksum ben formati
        assert src_checksums["row_count"] == len(rows)
        assert isinstance(src_checksums["md5_ordered"], str)
        assert len(src_checksums["md5_ordered"]) == 32

        # 4. Reconciliation: 3 metriche identiche, fail-loud diagnostico
        assert_reconciliation_match(src_checksums, dst_checksums)

    finally:
        # Cleanup robusto: drop sia src che dst anche se il writer ha
        # fallito mid-write. `IF EXISTS` rende le drop idempotenti.
        drop_table_if_exists(src_table)
        drop_table_if_exists(dst_table)
        engine.dispose()
