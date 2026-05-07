"""
Phase 2 err-dbwriter commit B — preserve exception chain in upsert auto-index.

Pre-fix: in `_batch_upsert`, quando `check_conn.execute(create_idx)`
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest
from sqlalchemy.exc import OperationalError

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping,
    ConflictPolicy,
    TransactionMode,
    WriteConfig,
    WriteMode,
    WriteResult,
    WriteStatus,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _make_upsert_writer() -> DatabaseWriter:
    """Writer postgres configurato per UPSERT con match_columns=['id']."""
    config = WriteConfig(
        connection_id="conn-1",
        schema_name="public",
        table_name="dest",
        write_mode=WriteMode.UPSERT,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        if_table_exists="fail",
        batch_size=100,
        match_columns=["id"],
        conflict_policy=ConflictPolicy.FORCE,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="id", dest_type="int"),
        ],
    )
    writer = object.__new__(DatabaseWriter)
    writer.config = config
    writer.db_type = "postgres"
    writer.engine = MagicMock()
    writer.ddl = MagicMock()
    writer.progress_callback = None
    return writer


def test_batch_upsert_preserves_idx_err_as_cause_when_auto_index_fails():
    """Pre-fix: raise ValueError(...) senza `from idx_err` → __cause__ None.
    Post-fix: raise ValueError(...) from idx_err → __cause__ == idx_err.

    Verifica davvero la chain preservata: non basta controllare il tipo
    ValueError, serve l'identita' dell'eccezione originale in __cause__.
    """
    writer = _make_upsert_writer()

    # Il blocco PRE _execute_in_batches apre `engine.begin() as check_conn`
    # e fa due chiamate a check_conn.execute:
    #   1) SELECT COUNT(*) ... information_schema — serve .scalar() == 0
    #      (nessun constraint esistente, forza il ramo CREATE INDEX)
    #   2) CREATE UNIQUE INDEX IF NOT EXISTS — deve sollevare
    constraint_probe = MagicMock()
    constraint_probe.scalar.return_value = 0  # no constraint → branch auto-create

    original_error = OperationalError(
        "CREATE UNIQUE INDEX ...",
        {},
        Exception("permission denied for schema public"),
    )

    check_conn = MagicMock()
    check_conn.execute.side_effect = [constraint_probe, original_error]

    begin_ctx = MagicMock()
    begin_ctx.__enter__.return_value = check_conn
    begin_ctx.__exit__.return_value = False
    writer.engine.begin.return_value = begin_ctx

    df = pd.DataFrame({"id": [1, 2, 3]})
    result = WriteResult(
        total_rows=len(df),
        table_name="public.dest",
        write_mode="upsert",
    )

    with pytest.raises(ValueError) as exc_info:
        writer._batch_upsert(df, writer.config.column_mappings, result)

    ve = exc_info.value

    # Assert 1: la stringa del ValueError menziona il motivo del fallimento
    # (contratto pre-esistente, non deve regredire).
    assert "indice UNIQUE" in str(ve)
    assert "creazione automatica" in str(ve)

    # Assert 2: __cause__ e' l'eccezione originale (la chain e' preservata).
    # Questo e' il punto del commit B. Pre-fix: __cause__ era None.
    assert ve.__cause__ is original_error, (
        f"Atteso __cause__ == idx_err originale (OperationalError), "
        f"ottenuto __cause__={ve.__cause__!r}. "
        f"Pre-fix: __cause__ era None perche' mancava `from idx_err`."
    )

    # Assert 3: la classe dell'eccezione sollevata resta ValueError
    # (commit B NON introduce una domain exception dedicata; quello
    # e' debito Batch successivi fuori perimetro).
    assert type(ve) is ValueError


def test_batch_upsert_skips_auto_index_when_constraint_already_exists():
    """Sanity: se il constraint esiste gia' (.scalar() > 0), il ramo
    CREATE INDEX non viene toccato e nessun ValueError viene sollevato
    in questo step. Garantisce che il commit B non altera il percorso
    felice in cui la chain non e' nemmeno in gioco.
    """
    writer = _make_upsert_writer()

    constraint_probe = MagicMock()
    constraint_probe.scalar.return_value = 1  # constraint esistente

    check_conn = MagicMock()
    check_conn.execute.return_value = constraint_probe

    begin_ctx = MagicMock()
    begin_ctx.__enter__.return_value = check_conn
    begin_ctx.__exit__.return_value = False
    writer.engine.begin.return_value = begin_ctx

    # ddl.generate_upsert + _execute_in_batches mockati: serve solo
    # assicurarsi che il blocco PRE non sollevi.
    writer.ddl.generate_upsert.return_value = ("INSERT ... ON CONFLICT ...", ["id"])
    writer._execute_in_batches = MagicMock(return_value=WriteResult(
        status=WriteStatus.SUCCESS,
        total_rows=3,
        table_name="public.dest",
        write_mode="upsert",
    ))

    df = pd.DataFrame({"id": [1, 2, 3]})
    result = WriteResult(
        total_rows=len(df),
        table_name="public.dest",
        write_mode="upsert",
    )

    # Non deve sollevare: il CREATE INDEX non parte
    writer._batch_upsert(df, writer.config.column_mappings, result)

    # check_conn.execute chiamato una sola volta (solo SELECT COUNT, no CREATE)
    assert check_conn.execute.call_count == 1
