"""Protocols + default implementations for ``FileSourceService`` DI.

Canonical STORAGE_ARTIFACT_ENGINE boundary for the file service dependencies.
"""

from __future__ import annotations

import shutil
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Protocol

from app.core.dataframes.default_loader import (
    DataLoaderProtocol as DataLoaderProtocol,
    DefaultDataLoader as DefaultDataLoader,
)

__all__ = [
    "LoggerProtocol",
    "FileSystemProtocol",
    "DataLoaderProtocol",
    "TimeProviderProtocol",
    "NullLogger",
    "DefaultTimeProvider",
    "DefaultFileSystem",
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


class FileSystemProtocol(Protocol):
    """Protocol per operazioni filesystem - mockabile per test.

    Punto 3 cleanup (2026-05-25 + 2026-05-26): tombstonati i metodi
    write-on-disk del protocol (zero caller runtime post refactor
    RAM-only):
      - `write_text` -> `write_binary(BytesIO(text.encode()))` (era qui)
      - `extract_zip` -> `zipfile.ZipFile(BytesIO(...))` +
        `validate_zip_bomb_limits` (era qui)
      - `write_binary` -> RAM-first (bytes -> storage backend); il
        protocol resta read-only/structural.
    Il `DefaultFileSystem` concreto sopravvive perche' istanziato come
    DI default da `FileSourceService` / `DatasourceService`, ma il
    payload upload viene letto dal blob storage in memoria, non da
    disco locale."""
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def iterdir(self, path: Path) -> List[Path]: ...
    def read_text(self, path: Path, encoding: str = 'utf-8') -> str: ...
    def stat_size(self, path: Path) -> int: ...
    def rmtree(self, path: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def rglob(self, path: Path, pattern: str) -> List[Path]: ...


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


class DefaultFileSystem:
    """Implementazione default filesystem"""

    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def iterdir(self, path: Path) -> List[Path]:
        return list(path.iterdir()) if path.exists() else []

    def read_text(self, path: Path, encoding: str = 'utf-8') -> str:
        return path.read_text(encoding=encoding)

    def stat_size(self, path: Path) -> int:
        return path.stat().st_size

    def rmtree(self, path: Path) -> None:
        shutil.rmtree(path)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def rglob(self, path: Path, pattern: str) -> List[Path]:
        return list(path.rglob(pattern))
