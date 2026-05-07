"""
S2 — introspection fail-fast su DatabaseWriter.

Pre-fix: le 4 funzioni `table_exists`, `get_existing_columns`,
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.exc import OperationalError

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    ColumnMapping,
    ConflictPolicy,
    TransactionMode,
    WriteConfig,
    WriteMode,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _make_writer(write_mode: WriteMode = WriteMode.APPEND) -> DatabaseWriter:
    """Writer costruito senza __init__ reale (niente engine SQLAlchemy vero).
    `ddl` e' un MagicMock con return_value coerenti per le 4 query."""
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
    writer.ddl.query_table_columns.return_value = ("SELECT 1", {})
    writer.ddl.query_list_schemas.return_value = "SELECT 1"
    writer.ddl.query_list_tables.return_value = ("SELECT 1", {})
    writer.progress_callback = None
    return writer


def _boom() -> OperationalError:
    return OperationalError("stmt", {}, Exception("permission denied"))


# Le 4 introspection non devono mascherare errori reali


def test_table_exists_propagates_engine_error():
    """Pre-fix: restituiva False (confuso con 'tabella non esiste').
    Post-fix: alza OperationalError."""
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer.table_exists()


def test_get_existing_columns_propagates_engine_error():
    """Pre-fix: restituiva [] (confuso con 'tabella senza colonne').
    Post-fix: alza OperationalError."""
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer.get_existing_columns()


def test_get_schemas_propagates_engine_error():
    """Pre-fix: restituiva [] (confuso con 'DB senza schemi').
    Post-fix: alza OperationalError."""
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer.get_schemas()


def test_get_tables_propagates_engine_error():
    """Pre-fix: restituiva [] (confuso con 'schema vuoto').
    Post-fix: alza OperationalError."""
    writer = _make_writer()
    writer.engine.connect.side_effect = _boom()

    with pytest.raises(OperationalError):
        writer.get_tables("public")


# validate() preserva il contratto Tuple[bool, str] traducendo il raise


def test_validate_reports_table_exists_failure_as_false_with_reason():
    """La raise di table_exists() dentro validate() non deve fuoriuscire:
    validate() cattura e ritorna (False, messaggio di verifica fallita).

    Distingue il messaggio post-fix da quello pre-fix: pre-fix diceva
    'la tabella non esiste', ora deve dichiarare esplicitamente che
    la *verifica* e' fallita (cosi' il client non confonde i due casi).
    """
    writer = _make_writer(write_mode=WriteMode.APPEND)
    ok_ctx = MagicMock()  # test connessione SELECT 1 ok
    writer.engine.connect.side_effect = [ok_ctx, _boom()]  # table_exists alza

    ok, msg = writer.validate()

    assert ok is False
    assert "verifica" in msg.lower() and "tabella" in msg.lower(), (
        f"Atteso messaggio esplicito 'verifica tabella fallita', "
        f"ottenuto: {msg!r}"
    )


def test_validate_reports_get_columns_failure_as_false_with_reason():
    """Se la tabella esiste ma la lettura colonne fallisce, validate()
    deve ritornare (False, motivo) e non mascherare l'errore come
    'mapping non valido' (che era il comportamento pre-fix: columns=[]
    faceva saltare il check 'if existing_col_names:' silenziosamente).
    """
    writer = _make_writer(write_mode=WriteMode.APPEND)
    # 1) SELECT 1 ok
    # 2) table_exists -> deve ritornare True. Configuriamo scalar>0.
    table_exists_ctx = MagicMock()
    table_exists_ctx.__enter__.return_value.execute.return_value.scalar.return_value = 1
    # 3) get_existing_columns -> alza
    writer.engine.connect.side_effect = [MagicMock(), table_exists_ctx, _boom()]

    ok, msg = writer.validate()

    assert ok is False
    assert "colonne" in msg.lower() or "columns" in msg.lower(), (
        f"Atteso messaggio esplicito su lettura colonne fallita, "
        f"ottenuto: {msg!r}"
    )
