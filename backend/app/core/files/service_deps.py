"""Protocols + default implementations for ``FileSourceService`` DI.

Post tombstone DefaultFileSystem (2026-05-26): l'unico DI rimasto e'
sui Logger / TimeProvider / DataLoader. `FileSystemProtocol` +
`DefaultFileSystem` rimossi — erano istanziati come default da
`FileSourceService` e `DatasourceService` e passati a
`preview_file_temp(fs=...)` che li ignorava esplicitamente (RAM-only
path non li usava). Storia dei tombstone:
  - 2026-05-25 (Punto 3): `write_text`, `extract_zip` rimossi.
  - 2026-05-26 (Punto 3 follow-up): `write_binary` rimosso.
  - 2026-05-26 (audit follow-up): `FileSystemProtocol` + intera classe
    `DefaultFileSystem` rimossi (zero caller runtime dei metodi
    `mkdir`/`exists`/`read_text`/`stat_size`/`rmtree`/`unlink`/`rglob`).
"""

from __future__ import annotations

import uuid

from datetime import datetime, timezone
from typing import Protocol

from app.core.dataframes.default_loader import (
    DataLoaderProtocol as DataLoaderProtocol,
    DefaultDataLoader as DefaultDataLoader,
)

__all__ = [
    "LoggerProtocol",
    "DataLoaderProtocol",
    "TimeProviderProtocol",
    "NullLogger",
    "DefaultTimeProvider",
    "DefaultDataLoader",
]


# Interfacce astratte per disaccoppiare il servizio dall'implementazione concreta
# In produzione: filesystem reale + GeoPandas. In test: mock in-memory


class LoggerProtocol(Protocol):
    """Protocol per logger"""
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...


class TimeProviderProtocol(Protocol):
    """Protocol per datetime/uuid - mockabile per test deterministici"""
    def now(self) -> datetime: ...
    def generate_uuid(self) -> str: ...


# Implementazioni concrete dei protocolli per uso in produzione e testing


class NullLogger:
    """Logger che non fa nulla - per test silenziosi"""
    def info(self, msg: str) -> None: pass
    def error(self, msg: str) -> None: pass
    def debug(self, msg: str) -> None: pass
    def warning(self, msg: str) -> None: pass


class DefaultTimeProvider:
    """Time provider di default - UTC aware (no naive datetime)."""
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def generate_uuid(self) -> str:
        # N1: full 32-hex UUID (pre-fix solo i primi 8 char = ~10^9
        # combinazioni -> brute-force fattibile se il nome + timestamp
        # sono noti). 32-hex = 2^128 = infeasible
        return uuid.uuid4().hex
