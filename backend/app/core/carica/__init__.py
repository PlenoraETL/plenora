"""
Modulo CARICA — Caricamento dati su database relazionali.

Fa parte della pipeline PLENORA-ETL: ESTRAI → MANIPOLA → ARRICCHISCI → [CARICA]
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
