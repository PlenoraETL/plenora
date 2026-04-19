"""WARNING difensivo su configurazione write rischiosa (C4).

Contract: se una WriteConfig combina:
  - write_mode = APPEND
  - transaction_mode = PER_BATCH
  - match_columns vuoti (no conflict detection)

il writer logga un WARNING loud al momento della write(). Questa
combinazione non e' bloccata (potrebbe essere legittima per tabelle
append-only con lineage esterno), ma e' rischiosa in caso di errore
parziale: i batch gia' committati restano scritti, e senza
match_columns un rilancio manuale duplicherebbe le righe.

Il WARNING serve per audit e per l'operatore che legge il log dopo
un fallimento: distinguere "pipeline fallita safely" (ALL_OR_NOTHING
rollback o APPEND con match_columns) da "potenziali righe duplicate
sul DB cliente — verificare prima di ri-lanciare".
"""

import logging
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.carica.database_writer import DatabaseWriter
from app.core.carica.models import (
    WriteConfig,
    ColumnMapping,
    ConflictPolicy,
    TransactionMode,
    WriteMode,
)


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _make_config(
    *,
    write_mode: WriteMode,
    transaction_mode: TransactionMode,
    match_columns=None,
) -> WriteConfig:
    return WriteConfig(
        connection_id="conn-1",
        schema_name="public",
        table_name="dest",
        write_mode=write_mode,
        transaction_mode=transaction_mode,
        if_table_exists="fail",
        batch_size=100,
        match_columns=match_columns or [],
        conflict_policy=ConflictPolicy.FORCE,
        column_mappings=[
            ColumnMapping(source_column="id", dest_column="id", dest_type="int"),
        ],
    )


def _make_writer(config: WriteConfig) -> DatabaseWriter:
    writer = object.__new__(DatabaseWriter)
    writer.config = config
    writer.engine = MagicMock()
    writer.db_type = "postgres"
    writer.ddl = MagicMock()
    writer.progress_callback = None
    return writer


@pytest.fixture
def df_small():
    return pd.DataFrame({"id": [1, 2, 3]})


# =============================================================================
# WARNING presente nei casi rischiosi
# =============================================================================


def test_warns_on_append_per_batch_without_match_columns(df_small):
    """Il caso pericoloso: append + per_batch + no match_columns → WARNING.

    Usa patch diretto su logger.warning: il logger custom di
    app.utils.logger non propaga a caplog."""
    config = _make_config(
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=[],
    )
    writer = _make_writer(config)

    with patch.object(writer, "_batch_insert", return_value=MagicMock()), \
         patch.object(writer, "_prepare_dataframe", return_value=df_small), \
         patch("app.core.carica.database_writer.logger") as mock_logger:
        writer.write(df_small)

    # Raccogli tutti i messaggi warning passati al logger
    warning_calls = [
        call.args[0] if call.args else ""
        for call in mock_logger.warning.call_args_list
    ]
    assert any(
        "append" in w.lower() and "per_batch" in w.lower() and "duplic" in w.lower()
        for w in warning_calls
    ), f"Nessun WARNING su unsafe append: {warning_calls}"


# =============================================================================
# Nessun WARNING sui casi safe
# =============================================================================


def _get_unsafe_warnings(mock_logger):
    """Raccogli i WARNING del mock che matchano il pattern unsafe."""
    return [
        (call.args[0] if call.args else "")
        for call in mock_logger.warning.call_args_list
        if call.args and "duplic" in call.args[0].lower()
        and "per_batch" in call.args[0].lower()
    ]


def test_no_warning_on_append_all_or_nothing(df_small):
    """ALL_OR_NOTHING → rollback su errore → no duplicazione → no WARNING."""
    config = _make_config(
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.ALL_OR_NOTHING,
        match_columns=[],
    )
    writer = _make_writer(config)

    with patch.object(writer, "_batch_insert", return_value=MagicMock()), \
         patch.object(writer, "_prepare_dataframe", return_value=df_small), \
         patch("app.core.carica.database_writer.logger") as mock_logger:
        writer.write(df_small)

    assert _get_unsafe_warnings(mock_logger) == []


def test_no_warning_on_append_per_batch_with_match_columns(df_small):
    """Con match_columns → conflict detection attiva → ri-run safe.
    Nessun WARNING (anche se per_batch)."""
    config = _make_config(
        write_mode=WriteMode.APPEND,
        transaction_mode=TransactionMode.PER_BATCH,
        match_columns=["id"],
    )
    writer = _make_writer(config)

    with patch.object(writer, "_batch_insert", return_value=MagicMock()), \
         patch.object(writer, "_prepare_dataframe", return_value=df_small), \
         patch.object(writer, "check_conflicts", return_value=MagicMock(has_conflicts=False)), \
         patch("app.core.carica.database_writer.logger") as mock_logger:
        writer.write(df_small)

    assert _get_unsafe_warnings(mock_logger) == []


def test_no_warning_on_create_or_replace(df_small):
    """CREATE / REPLACE → tabella viene droppata → no rischio duplicazione."""
    for mode in (WriteMode.CREATE, WriteMode.REPLACE):
        config = _make_config(
            write_mode=mode,
            transaction_mode=TransactionMode.PER_BATCH,
            match_columns=[],
        )
        writer = _make_writer(config)

        with patch.object(writer, "_batch_insert", return_value=MagicMock()), \
             patch.object(writer, "_prepare_dataframe", return_value=df_small), \
             patch.object(writer, "drop_table"), \
             patch.object(writer, "create_table"), \
             patch("app.core.carica.database_writer.logger") as mock_logger:
            writer.write(df_small)

        assert _get_unsafe_warnings(mock_logger) == [], (
            f"WARNING spurio su {mode.value}"
        )
