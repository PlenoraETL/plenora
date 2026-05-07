"""
Phase 2 err-dbwriter commit A — fail-closed su preflight/routing silenziosi.

Pre-fix: tre entry point di preflight/routing in `DatabaseWriter`
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
    WriteStatus,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _make_writer(write_mode: WriteMode = WriteMode.APPEND) -> DatabaseWriter:
    """Writer costruito senza __init__ reale (niente engine SQLAlchemy vero).
    Ricalca la convenzione di `test_database_writer_introspection_failfast_contract`.
    """
    config = WriteConfig(
        connection_id="conn-1",
        schema_name="public",
        table_name="dest",
        write_mode=write_mode,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        if_table_exists="fail",
        batch_size=100,
        match_columns=[],
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
    writer.ddl.query_table_exists.return_value = ("SELECT 1", {})
    writer.ddl.generate_truncate.return_value = "TRUNCATE TABLE public.dest"
    writer.ddl.generate_drop_table.return_value = "DROP TABLE public.dest"
    writer.ddl.qualified_table_name.return_value = "public.dest"
    writer.progress_callback = None
    return writer


def _boom() -> OperationalError:
    return OperationalError("stmt", {}, Exception("lock wait timeout"))


# _check_create_permission: fail-closed su errori non-permission


def test_check_create_permission_fail_closed_on_unknown_error():
    """Pre-fix: logger.warning + return (True, "") — lasciava passare.
    Post-fix: return (False, msg) con motivo esplicito.
    """
    writer = _make_writer(write_mode=WriteMode.CREATE)
    # Simulo errore transient non classificabile come permission
    writer.engine.connect.side_effect = _boom()

    ok, msg = writer._check_create_permission()

    assert ok is False, (
        "Pre-fix ritornava True su errore sconosciuto (smell A): "
        "validator che greenlight senza aver verificato."
    )
    assert "verifica" in msg.lower() and "create" in msg.lower(), (
        f"Atteso messaggio che dichiara esplicitamente 'verifica fallita', "
        f"ottenuto: {msg!r}"
    )


def test_check_create_permission_preserves_permission_denied_branch():
    """Sanity: il ramo 'permesso mancante' (quello gia' fail-closed pre-fix)
    resta invariato. Il commit A non deve regredire il caso felice-noto.
    """
    writer = _make_writer(write_mode=WriteMode.CREATE)
    writer.engine.connect.side_effect = OperationalError(
        "stmt", {}, Exception("permission denied for relation")
    )

    ok, msg = writer._check_create_permission()

    assert ok is False
    assert "permess" in msg.lower(), (
        f"Atteso messaggio 'permessi insufficienti', ottenuto: {msg!r}"
    )


# truncate_table: solleva invece di ritornare False


def test_truncate_table_raises_instead_of_returning_false():
    """Pre-fix: logger.error + return False — in write() PER_BATCH
    il caller procedeva con INSERT appendendo anziche' truncate-inserting.
    Post-fix: raise propaga al boundary in write().
    """
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer.truncate_table()


# _table_exists_for: solleva invece di ritornare False silenziosamente


def test_table_exists_for_raises_instead_of_returning_false():
    """Pre-fix: logger.warning + return False — usato da _replace_atomic
    per scegliere swap vs single-rename, silenziare era routing errato.
    Post-fix: allineato a self.table_exists (gia' failfast), propaga.
    """
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer._table_exists_for("public", "some_target")


# Boundary end-to-end: write() PER_BATCH TRUNCATE_INSERT con truncate fallito


def test_write_truncate_insert_per_batch_reports_write_error_when_truncate_fails():
    """Pre-fix: truncate_table falliva silent → _batch_insert appendeva →
    WriteResult.SUCCESS con dati duplicati (correttezza compromessa).
    Post-fix: truncate_table solleva → boundary in write() cattura →
    WriteResult.status = WRITE_ERROR con error_message popolato.

    Verifica il contratto end-to-end: l'errore osservabile cambia da
    'SUCCESS con side-effect silenzioso' a 'WRITE_ERROR esplicito'.
    """
    config = WriteConfig(
        connection_id="conn-1",
        schema_name="public",
        table_name="dest",
        write_mode=WriteMode.TRUNCATE_INSERT,
        transaction_mode=TransactionMode.PER_BATCH,
        if_table_exists="fail",
        batch_size=100,
        match_columns=[],
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
    writer.ddl.generate_truncate.return_value = "TRUNCATE TABLE public.dest"
    writer.progress_callback = None
    # engine.connect() alza su TRUNCATE (qualunque errore transient)
    writer.engine.connect.side_effect = _boom()

    df = pd.DataFrame({"id": [1, 2, 3]})
    result = writer.write(df)

    assert result.status == WriteStatus.WRITE_ERROR, (
        f"Pre-fix: truncate silent + INSERT append → SUCCESS con "
        f"dati duplicati. Post-fix atteso WRITE_ERROR, ottenuto "
        f"{result.status!r}."
    )
    assert result.error_message, (
        "error_message deve contenere l'errore originale propagato "
        "dal truncate fallito."
    )
