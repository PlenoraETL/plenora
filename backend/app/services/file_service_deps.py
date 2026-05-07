"""Protocols + default implementations per FileSourceService DI (STRUCT-001 Seam B).

Estratto verbatim da `app.services.file_service` senza alcun cambio
comportamentale. `file_service.py` re-esporta gli 8 simboli per
backward-compat con i test e con `FileSourceService.__init__`
(continua a istanziare i Default come fallback).

Contenuto:
  - 4 Protocols (DI interfaces): LoggerProtocol, FileSystemProtocol,
    DataLoaderProtocol, TimeProviderProtocol.
  - 4 Default implementations (production-ready, stdlib + pandas):
    NullLogger, DefaultTimeProvider, DefaultFileSystem, DefaultDataLoader.

Leaf module: zero `app.services.*` imports. Lazy imports preservati
per zip_safe, geopandas, fiona (cycle/optional-dep safety).

Go-portability: le 4 Protocols mappano 1:1 a Go interface
(`type FileSystem interface { Mkdir(...); ... }`) - chiarisce il
contract DI verso un eventuale control-plane Go.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, List, Optional, Protocol

import pandas as pd


# Logger stdlib: file_service_deps e' un leaf module e non deve dipendere
# da `app.utils.logger`. Usato solo per debug-log non critico in
# `DefaultDataLoader.list_layers`/`list_sheets`.
_logger = logging.getLogger(__name__)


# Interfacce astratte per disaccoppiare il servizio dall'implementazione concreta.
# In produzione: filesystem reale + GeoPandas. In test: mock in-memory.


class LoggerProtocol(Protocol):
    """Protocol per logger"""
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...


class FileSystemProtocol(Protocol):
    """Protocol per operazioni filesystem - mockabile per test"""
    def mkdir(self, path: Path, parents: bool = True, exist_ok: bool = True) -> None: ...
    def exists(self, path: Path) -> bool: ...
    def is_dir(self, path: Path) -> bool: ...
    def is_file(self, path: Path) -> bool: ...
    def iterdir(self, path: Path) -> List[Path]: ...
    def read_text(self, path: Path, encoding: str = 'utf-8') -> str: ...
    def write_text(self, path: Path, content: str, encoding: str = 'utf-8') -> None: ...
    def write_binary(self, path: Path, content: BinaryIO) -> int: ...
    def stat_size(self, path: Path) -> int: ...
    def rmtree(self, path: Path) -> None: ...
    def unlink(self, path: Path) -> None: ...
    def extract_zip(self, zip_path: Path, dest_dir: Path) -> None: ...
    def rglob(self, path: Path, pattern: str) -> List[Path]: ...


class DataLoaderProtocol(Protocol):
    """Protocol per caricamento dati - mockabile per test"""
    def load_csv(self, path: Path) -> pd.DataFrame: ...
    def load_excel(self, path: Path, sheet: Optional[str] = None) -> pd.DataFrame: ...
    def load_json(self, path: Path) -> pd.DataFrame: ...
    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> Any: ...
    def list_layers(self, path: Path) -> List[str]: ...
    def list_sheets(self, path: Path) -> List[str]: ...
    def save_csv(self, df: pd.DataFrame, path: Path) -> None: ...
    def save_excel(self, df: pd.DataFrame, path: Path, sheet: str) -> None: ...
    def save_json(self, df: pd.DataFrame, path: Path) -> None: ...
    def save_geospatial(self, gdf: Any, path: Path, driver: str, layer: Optional[str] = None) -> None: ...


class TimeProviderProtocol(Protocol):
    """Protocol per datetime/uuid - mockabile per test deterministici"""
    def now(self) -> datetime: ...
    def generate_uuid(self) -> str: ...


# Implementazioni concrete dei protocolli per uso in produzione e testing.


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
        # sono noti). 32-hex = 2^128 = infeasible.
        import uuid
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

    def write_text(self, path: Path, content: str, encoding: str = 'utf-8') -> None:
        path.write_text(content, encoding=encoding)

    def write_binary(self, path: Path, content: BinaryIO) -> int:
        import shutil
        with open(path, 'wb') as f:
            shutil.copyfileobj(content, f)
        return path.stat().st_size

    def stat_size(self, path: Path) -> int:
        return path.stat().st_size

    def rmtree(self, path: Path) -> None:
        import shutil
        shutil.rmtree(path)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def extract_zip(self, zip_path: Path, dest_dir: Path) -> None:
        """Estrae un archivio ZIP con guard zip slip + zip bomb.

        Delega a `app.utils.zip_safe.safe_extract_zip_path` per evitare
        duplicazione: stesso codepath di tutti gli altri estrattori del
        progetto (file_service blob, dataframe utils, imdf storage/loader).
        """
        from app.utils.zip_safe import safe_extract_zip_path
        safe_extract_zip_path(zip_path, dest_dir)

    def rglob(self, path: Path, pattern: str) -> List[Path]:
        return list(path.rglob(pattern))


class DefaultDataLoader:
    """Implementazione default data loader"""

    def load_csv(self, path: Path) -> pd.DataFrame:
        """
        Carica CSV con rilevamento automatico di encoding e delimitatore.
        Ordine tentativi:
          1. UTF-8 + sep auto (sniff)
          2. UTF-8 + ; esplicito
          3. Latin-1 + sep auto (sniff)
          4. Latin-1 + ; esplicito
        """
        import csv as _csv

        def _detect_sep(path, encoding):
            try:
                with open(path, encoding=encoding, errors='replace') as f:
                    sample = f.read(4096)
                dialect = _csv.Sniffer().sniff(sample, delimiters=',;\t|')
                return dialect.delimiter
            except Exception:
                return ','

        for encoding in ['utf-8', 'latin-1', 'cp1252']:
            try:
                sep = _detect_sep(path, encoding)
                df = pd.read_csv(path, sep=sep, encoding=encoding)
                # Sanity check: se una sola colonna e contiene il delimitatore, riprova con ;
                if len(df.columns) == 1 and (';' in str(df.columns[0]) or ',' in str(df.columns[0])):
                    for alt_sep in [';', ',', '\t']:
                        try:
                            df2 = pd.read_csv(path, sep=alt_sep, encoding=encoding)
                            if len(df2.columns) > 1:
                                return df2
                        except Exception:
                            continue
                return df
            except UnicodeDecodeError:
                continue
            except Exception:
                continue

        # Ultimo fallback
        return pd.read_csv(path, encoding='latin-1', sep=';', on_bad_lines='skip')

    def load_excel(self, path: Path, sheet: Optional[str] = None) -> pd.DataFrame:
        if sheet:
            return pd.read_excel(path, sheet_name=sheet)
        return pd.read_excel(path)

    def load_json(self, path: Path) -> pd.DataFrame:
        try:
            return pd.read_json(path, orient='records')
        except (ValueError, TypeError):
            # orient='records' fallisce se il JSON e' un dict top-level (es.
            # {"key": [...]}) o ha schema non tabulare. Retry senza orient
            # per lasciare a pandas l'inferenza.
            return pd.read_json(path)

    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> Any:
        import geopandas as gpd
        if layer:
            return gpd.read_file(path, layer=layer)
        return gpd.read_file(path)

    def list_layers(self, path: Path) -> List[str]:
        try:
            import fiona
            return fiona.listlayers(str(path))
        except Exception as e:
            # Probe fiona: in formati non multi-layer ritorna [] (contratto).
            # Debug log preserva visibilita' su fallimenti reali (file corrotto,
            # permessi) che oggi si perdevano.
            _logger.debug(f"list_layers probe fiona fallita per {path}: {e}")
            return []

    def list_sheets(self, path: Path) -> List[str]:
        try:
            xlsx = pd.ExcelFile(path)
            return xlsx.sheet_names
        except Exception as e:
            _logger.debug(f"list_sheets probe pandas fallita per {path}: {e}")
            return []

    def save_csv(self, df: pd.DataFrame, path: Path) -> None:
        # UTF-8 esplicito: contratto leggibile, no ambiguita' su locale server.
        df.to_csv(path, index=False, encoding="utf-8")

    def save_excel(self, df: pd.DataFrame, path: Path, sheet: str) -> None:
        df.to_excel(path, index=False, sheet_name=sheet)

    def save_json(self, df: pd.DataFrame, path: Path) -> None:
        df.to_json(path, orient='records', indent=2)

    def save_geospatial(self, gdf: Any, path: Path, driver: str, layer: Optional[str] = None) -> None:
        if layer:
            gdf.to_file(path, driver=driver, layer=layer)
        else:
            gdf.to_file(path, driver=driver)
