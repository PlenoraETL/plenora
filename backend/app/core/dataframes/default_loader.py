"""Default pandas/geopandas data loader for file-source services."""

from __future__ import annotations

import csv as _csv
import logging
from pathlib import Path
from typing import Any, List, Optional

import pandas as pd

from app.core.dataframes.ports import DataLoaderProtocol
from app.core.dataframes.tabular import coerce_loaded_scalar_columns, read_excel_safe

_logger = logging.getLogger(__name__)

__all__ = ["DataLoaderProtocol", "DefaultDataLoader"]


class DefaultDataLoader:
    """Implementazione default data loader."""

    def load_csv(self, path: Path) -> pd.DataFrame:
        """
        Carica CSV con rilevamento automatico di encoding e delimitatore.
        Ordine tentativi:
          1. UTF-8 + sep auto (sniff)
          2. UTF-8 + ; esplicito
          3. Latin-1 + sep auto (sniff)
          4. Latin-1 + ; esplicito
        """

        def _detect_sep(path, encoding):
            try:
                with open(path, encoding=encoding, errors="replace") as f:
                    sample = f.read(4096)
                dialect = _csv.Sniffer().sniff(sample, delimiters=",;\t|")
                return dialect.delimiter
            except Exception:
                return ","

        for encoding in ["utf-8", "latin-1", "cp1252"]:
            try:
                sep = _detect_sep(path, encoding)
                df = coerce_loaded_scalar_columns(pd.read_csv(
                    path, sep=sep, encoding=encoding, dtype=object,
                    keep_default_na=False, na_values=[""],
                ))
                # Sanity check: se una sola colonna e contiene il delimitatore,
                # riprova con separatori comuni prima di accettare il fallback.
                if len(df.columns) == 1 and (";" in str(df.columns[0]) or "," in str(df.columns[0])):
                    for alt_sep in [";", ",", "\t"]:
                        try:
                            df2 = coerce_loaded_scalar_columns(pd.read_csv(
                                path, sep=alt_sep, encoding=encoding, dtype=object,
                                keep_default_na=False, na_values=[""],
                            ))
                            if len(df2.columns) > 1:
                                return df2
                        except Exception:
                            continue
                return df
            except UnicodeDecodeError:
                continue
            except Exception:
                continue

        # Fallback permissivo last-resort: scarta le righe malformate. È lossy →
        # NON deve essere silenzioso (Backend master Part II #3): WARNING esplicito.
        # Niente conteggio via open() diretto: rispetta il boundary I/O
        # (app/core/io) — il segnale chiave e' che il path lossy e' stato usato.
        df = coerce_loaded_scalar_columns(pd.read_csv(
            path, encoding="latin-1", sep=";", on_bad_lines="skip",
            dtype=object, keep_default_na=False, na_values=[""],
        ))
        _logger.warning(
            "CSV %s: usato il fallback permissivo (on_bad_lines=skip): eventuali "
            "righe malformate sono state scartate",
            path,
        )
        return df

    def load_excel(self, path: Path, sheet: Optional[str] = None) -> pd.DataFrame:
        return read_excel_safe(path, sheet=sheet)

    def load_json(self, path: Path) -> pd.DataFrame:
        try:
            return pd.read_json(path, orient="records")
        except (ValueError, TypeError):
            # orient='records' fallisce se il JSON e' un dict top-level (es
            # {"key": [...]}) o ha schema non tabulare. Retry senza orient
            # per lasciare a pandas l'inferenza.
            return pd.read_json(path)

    def load_parquet(self, path: Path) -> pd.DataFrame:
        return pd.read_parquet(path)

    def load_geospatial(self, path: Path, layer: Optional[str] = None) -> Any:
        import geopandas as gpd

        if layer:
            return gpd.read_file(path, layer=layer, engine="pyogrio")
        return gpd.read_file(path, engine="pyogrio")

    def list_layers(self, path: Path) -> List[str]:
        try:
            import pyogrio

            infos = pyogrio.list_layers(str(path))
            return [str(row[0]) for row in infos]
        except Exception as e:
            # Probe pyogrio: in formati non multi-layer ritorna [].
            # Debug log preserva visibilita' su fallimenti reali.
            _logger.debug(f"list_layers probe pyogrio fallita per {path}: {e}")
            return []

    def list_sheets(self, path: Path) -> List[str]:
        try:
            xlsx = pd.ExcelFile(path)
            return xlsx.sheet_names
        except Exception as e:
            _logger.debug(f"list_sheets probe pandas fallita per {path}: {e}")
            return []
