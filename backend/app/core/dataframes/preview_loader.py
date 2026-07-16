"""Bounded loader per preview file, senza RAM amplification.

Dispatch format-specific con cap esplicito sul numero di righe/feature
caricate: la preview legge solo quello che serve.

API:
  - `preview_dataframe_from_bytes(data, format, *, max_rows, layer, sheet)`
    Ritorna `(df_preview, total_row_count_or_None)`:
      - `df_preview`: DataFrame con AT MOST `max_rows` righe.
      - `total_row_count_or_None`: row count totale se il formato espone
        metadata cheap (parquet, gpkg multi-layer via pyogrio.read_info);
        `None` se la stima richiederebbe scan completo (csv, json).
        Il caller front-end deve interpretare `None` come "row count
        non disponibile in preview, riprovare dopo upload definitivo".
"""

from __future__ import annotations

import json
from io import BytesIO, StringIO
from typing import Any, BinaryIO, Optional, Tuple

import pandas as pd

from app.core.dataframes.tabular import coerce_loaded_scalar_columns, read_excel_safe


_DEFAULT_PREVIEW_MAX_ROWS = 200


def _normalize_format(format_hint: str) -> str:
    if not format_hint:
        return ""
    f = format_hint.strip().lower().lstrip(".")
    return "gpkg" if f == "geopackage" else f


def _preview_csv(data: bytes, max_rows: int) -> Tuple[pd.DataFrame, Optional[int]]:
    """CSV bounded: usa `nrows=max_rows` di pandas. Total count non
    disponibile senza scan completo — ritorniamo None (frontend
    interpreti come 'row count non disponibile in preview')."""
    from app.core.dataframes.tabular import _CSV_ENCODINGS

    last_err: Optional[UnicodeDecodeError] = None
    for enc in _CSV_ENCODINGS:
        try:
            df = pd.read_csv(
                BytesIO(data),
                low_memory=False,
                encoding=enc,
                nrows=max_rows,
                dtype=object,
                keep_default_na=False,
                na_values=[""],
            )
            return coerce_loaded_scalar_columns(df), None
        except UnicodeDecodeError as e:
            last_err = e
            continue
    raise UnicodeDecodeError(
        last_err.encoding if last_err else "utf-8",
        last_err.object if last_err else b"",
        last_err.start if last_err else 0,
        last_err.end if last_err else 0,
        f"CSV non decodificabile con {_CSV_ENCODINGS}",
    )


def _preview_excel(
    data: bytes,
    max_rows: int,
    sheet: Optional[str],
) -> Tuple[pd.DataFrame, Optional[int]]:
    """XLSX bounded: pandas `read_excel(nrows=max_rows)`. Total count
    richiederebbe parse completo, ritorna None."""
    return read_excel_safe(BytesIO(data), sheet=sheet, nrows=max_rows), None


def _preview_parquet(data: bytes, max_rows: int) -> Tuple[pd.DataFrame, Optional[int]]:
    """Parquet bounded: pyarrow `ParquetFile().iter_batches(batch_size=max_rows)`
    preleva il primo batch + ritorna `metadata.num_rows` (cheap, gia'
    in footer)."""
    import pyarrow.parquet as pq

    pf = pq.ParquetFile(BytesIO(data))
    total = int(pf.metadata.num_rows) if pf.metadata is not None else None
    batches = pf.iter_batches(batch_size=max_rows)
    try:
        first_batch = next(batches)
    except StopIteration:
        # File vuoto: dataframe vuoto con schema corretto.
        return pf.schema_arrow.empty_table().to_pandas(), 0
    df = first_batch.to_pandas()
    # Best-effort: applica dtype_backend nullable come il path full
    # (preserva int64 con null come pd.NA invece di float NaN).
    try:
        df = df.convert_dtypes(dtype_backend="numpy_nullable")
    except Exception:
        pass
    return df, total


def _preview_json(data: bytes, max_rows: int) -> Tuple[pd.DataFrame, Optional[int]]:
    """JSON bounded: per JSON-lines pandas supporta `nrows`; per JSON
    standard dobbiamo caricare e truncare (no streaming nel format).
    JSON normale e' raro per file >100MB, accettiamo questa limitazione."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("utf-8-sig")
    # Heuristic JSON-lines: prima riga e' un JSON object completo, e
    # ci sono piu' linee non vuote.
    stripped = text.strip()
    if stripped and "\n" in stripped:
        first = stripped.splitlines()[0].strip()
        if first.startswith("{") and first.endswith("}"):
            try:
                df = pd.read_json(StringIO(text), lines=True, nrows=max_rows)
                return df, None
            except Exception:
                pass
    # JSON standard: parse + truncate.
    parsed = json.loads(text)
    if isinstance(parsed, list):
        truncated = parsed[:max_rows]
        return pd.DataFrame(truncated), len(parsed)
    df = pd.DataFrame(parsed)
    return df.head(max_rows), len(df)


def _preview_vector(
    data: bytes,
    max_rows: int,
    layer: Optional[str],
) -> Tuple[pd.DataFrame, Optional[int]]:
    """Geospatial bounded: pyogrio `read_dataframe(max_features=max_rows)`.
    Total count via `read_info` (cheap metadata read)."""
    import pyogrio

    info = None
    try:
        info = pyogrio.read_info(data, layer=layer)
    except Exception:
        info = None
    total = int(info["features"]) if info and "features" in info else None

    gdf = pyogrio.read_dataframe(data, layer=layer, max_features=max_rows)
    return gdf, total


def preview_dataframe_from_bytes(
    data: bytes,
    format_hint: str,
    *,
    max_rows: int = _DEFAULT_PREVIEW_MAX_ROWS,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
) -> Tuple[Any, Optional[int]]:
    """Bounded preview: ritorna DataFrame con `<= max_rows` righe.

    Args:
        data: bytes del payload.
        format_hint: 'csv' / 'json' / 'xlsx' / 'xls' / 'parquet' /
            'geojson' / 'gpkg' / 'shp' / 'gdb' / 'zip'.
        max_rows: cap righe/feature da caricare (default 200).
        layer: nome layer per gpkg/gdb/shp multi-layer.
        sheet: nome foglio per xlsx multi-sheet.

    Returns:
        `(df, total_row_count_or_None)`:
          - `df`: DataFrame (o GeoDataFrame se geospatial) con cap.
          - `total_row_count_or_None`: row count totale del payload
            quando il format espone metadata cheap (parquet, geospatial,
            json standard); `None` per csv/jsonlines/xlsx dove serve
            scan completo che vanifica la preview bounded.

    Raises:
        ValueError: format_hint non supportato.
    """
    fmt = _normalize_format(format_hint)

    if fmt == "csv":
        return _preview_csv(data, max_rows)
    if fmt == "json":
        return _preview_json(data, max_rows)
    if fmt in ("xlsx", "xls"):
        return _preview_excel(data, max_rows, sheet)
    if fmt == "parquet":
        return _preview_parquet(data, max_rows)
    if fmt in ("geojson", "gpkg", "shp", "gdb", "zip"):
        return _preview_vector(data, max_rows, layer)

    raise ValueError(f"preview_dataframe_from_bytes: formato non supportato: {fmt!r}")


def preview_dataframe_from_stream(
    stream: BinaryIO,
    format_hint: str,
    *,
    max_rows: int = _DEFAULT_PREVIEW_MAX_ROWS,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
) -> Tuple[Any, Optional[int]]:
    """Bounded preview da file-like seekable, senza copia completa del BLOB."""
    fmt = _normalize_format(format_hint)
    stream.seek(0)

    if fmt == "csv":
        from app.core.dataframes.tabular import _CSV_ENCODINGS

        last_error = None
        for encoding in _CSV_ENCODINGS:
            stream.seek(0)
            try:
                loaded = pd.read_csv(
                    stream,
                    low_memory=False,
                    encoding=encoding,
                    nrows=max_rows,
                    dtype=object,
                    keep_default_na=False,
                    na_values=[""],
                )
                return coerce_loaded_scalar_columns(loaded), None
            except UnicodeDecodeError as exc:
                last_error = exc
        assert last_error is not None
        raise last_error
    if fmt in ("xlsx", "xls"):
        return read_excel_safe(stream, sheet=sheet, nrows=max_rows), None
    if fmt == "parquet":
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(stream)
        total = int(pf.metadata.num_rows) if pf.metadata is not None else None
        batches = pf.iter_batches(batch_size=max_rows)
        try:
            batch = next(batches)
        except StopIteration:
            return pf.schema_arrow.empty_table().to_pandas(), 0
        return batch.to_pandas(), total
    if fmt == "json":
        # pandas legge direttamente dal file-like. Per JSON-lines nrows evita
        # lo scan completo; il JSON array standard richiede comunque parsing
        # strutturale del documento, ma non una seconda copia bytes.
        try:
            stream.seek(0)
            return pd.read_json(stream, lines=True, nrows=max_rows), None
        except Exception:
            stream.seek(0)
            df = pd.read_json(stream)
            return df.head(max_rows), len(df)
    if fmt in ("geojson", "gpkg", "shp", "gdb", "zip"):
        import pyogrio

        info = None
        try:
            info = pyogrio.read_info(stream, layer=layer)
        except Exception:
            info = None
        stream.seek(0)
        total = int(info["features"]) if info and "features" in info else None
        return pyogrio.read_dataframe(
            stream,
            layer=layer,
            max_features=max_rows,
        ), total
    raise ValueError(
        f"preview_dataframe_from_stream: formato non supportato: {fmt!r}"
    )


__all__ = [
    "preview_dataframe_from_bytes",
    "preview_dataframe_from_stream",
]
