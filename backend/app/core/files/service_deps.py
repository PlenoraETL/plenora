"""Protocols + default implementations for ``FileSourceService`` DI."""

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
        # Full 32-hex UUID: 2^128 spazio di ricerca, non enumerabile.
        return uuid.uuid4().hex
