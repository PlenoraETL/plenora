"""
Modulo CARICA — Caricamento dati su database relazionali.

Fa parte della pipeline PLENORA-ETL: ESTRAI → MANIPOLA → ARRICCHISCI → [CARICA]

Il modulo CARICA è un nodo canvas collegabile solo al nodo Output (Result).
Riceve il DataFrame prodotto dalla pipeline e lo scrive su una tabella
del database destinazione configurato dall'utente.

Componenti:
- models.py: dataclass e enum (WriteMode, WriteConfig, WriteResult, ecc.)
- writer_base.py: ABC DestinationWriter (interfaccia per tutte le destinazioni)
- ddl_generator.py: generazione DDL multi-database
- column_mapper.py: auto-mapping colonne sorgente → destinazione
- conflict_checker.py: pre-check conflitti PK/UNIQUE
- database_writer.py: implementazione DatabaseWriter (SQLAlchemy)
- executor.py: entry point (parsing canvas, orchestrazione, audit)

Task asincrono: backend/workers/tasks/carica_task.py
Router API: app/core/carica/router.py
"""

# Models
from .models import (
    WriteMode,
    WriteStatus,
    TransactionMode,
    ConflictPolicy,
    ColumnMapping,
    WriteConfig,
    WriteResult,
    ConflictReport,
    ConflictDetail,
    AuditRecord,
    BatchProgress,
)

# ABC
from .writer_base import DestinationWriter

# Implementations
from .ddl_generator import DDLGenerator
from .column_mapper import ColumnMapper
from .conflict_checker import ConflictChecker
from .database_writer import DatabaseWriter

# Entry point
from .executor import CaricaExecutor

__all__ = [
    # Enums
    "WriteMode",
    "WriteStatus", 
    "TransactionMode",
    "ConflictPolicy",
    # Dataclasses
    "ColumnMapping",
    "WriteConfig",
    "WriteResult",
    "ConflictReport",
    "ConflictDetail",
    "AuditRecord",
    "BatchProgress",
    # ABC
    "DestinationWriter",
    # Implementations
    "DDLGenerator",
    "ColumnMapper",
    "ConflictChecker",
    "DatabaseWriter",
    # Entry point
    "CaricaExecutor",
]
