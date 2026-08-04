"""DataFrame serialization with minimal RAM amplification.

Evita pattern che creano piu' copie intermedie:

    buf = io.BytesIO()
    buf.write(df.to_csv(index=False).encode('utf-8'))  # 3 copie intermedie
    content = buf.getvalue()  # 4a copia

Peak RAM ~4-5x del df serializzato:
  1. `df` pandas (input)
  2. `df.to_csv()` ritorna stringa Python (~1.5x del df)
  3. `.encode('utf-8')` crea bytes (~1x della stringa)
  4. `buf.write(...)` BytesIO accumula i bytes (~1x)
  5. `buf.getvalue()` copia il contenuto di BytesIO (~1x finale)

Scrittura diretta sul `BytesIO` via `io.TextIOWrapper` per i text-format
(CSV/JSON), senza stringa Python intermedia. Picco ~2x (df + bytes finali).

Per Excel il path result usa XlsxWriter ``constant_memory`` direttamente su
uno stream binario: evita sia il workbook object-model completo sia il
``BytesIO`` contenente l'intero ZIP.

API:
  - `dataframe_to_export_bytes(df, dest_format, *, chunksize)`:
    serializza il DataFrame nel formato richiesto, ritorna i bytes.
    Usa il path minimal-copy (TextIOWrapper) per text formats.
"""

from __future__ import annotations

import io
import json
import math
import queue
import re
import threading
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import numpy as np
import pandas as pd

from app.core.dataframes.tabular import (
    normalize_object_decimals,
    sanitize_for_json,
)


_DEFAULT_CHUNKSIZE = 10_000
_XLSX_MAX_ROWS = 1_048_576
_XLSX_MAX_COLUMNS = 16_384
_DIRTY_TEXT_CONTROLS = re.compile(r"[\x00-\x1f\u2028\u2029]+")
_XLSX_WRITE_GENERIC = 0
_XLSX_WRITE_INTEGER = 1
_XLSX_WRITE_FLOAT = 2
_XLSX_WRITE_BOOLEAN = 3
_XLSX_WRITE_STRING = 4


def _clean_export_text(value: str) -> str:
    """Flatten control/newline noise and remove fixed-width right padding."""

    if _DIRTY_TEXT_CONTROLS.search(value):
        value = _DIRTY_TEXT_CONTROLS.sub(" ", value)
    return value.rstrip()


def _clean_export_text_chunk(df: Any) -> Any:
    """Normalize textual cells on a bounded frame slice without mutating input."""

    if df is None or df.empty:
        return df
    result = df.copy(deep=False)
    changed = False
    for index, dtype in enumerate(df.dtypes):
        if not (
            pd.api.types.is_object_dtype(dtype)
            or pd.api.types.is_string_dtype(dtype)
        ):
            continue
        series = df.iloc[:, index]
        cleaned = series.map(
            lambda value: _clean_export_text(value)
            if isinstance(value, str)
            else value
        )
        if not cleaned.equals(series):
            if not changed:
                result = df.copy(deep=False)
                changed = True
            result.isetitem(index, cleaned)
    return result


def normalize_export_format(dest_format: str) -> str:
    """Return the canonical export format used for payload and metadata.

    Legacy aliases are mapped to the actual writer format. This prevents
    OOXML/GPKG payloads from being exposed with misleading extensions.
    """
    fmt = (dest_format or "").strip().lower().lstrip(".")
    aliases = {
        "xls": "xlsx",
        "geopackage": "gpkg",
    }
    return aliases.get(fmt, fmt)


def _is_declared_excel_temporal(raw_type: Any) -> bool:
    logical = str(raw_type or "").strip().lower()
    return logical in {"date", "datetime", "timestamp"} or logical.startswith(
        "datetime64"
    )


def _coerce_declared_excel_temporal(value: Any, raw_type: Any) -> Any:
    """Parse text only when its column is explicitly declared temporal."""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return None
    try:
        # The application accepts Italian day-first input, while ISO strings
        # must retain year-first semantics (dayfirst=True swaps ambiguous ISO
        # month/day values in pandas 3).
        iso_year_first = re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}", text)
        parsed = pd.to_datetime(
            text,
            format="mixed",
            dayfirst=not bool(iso_year_first),
            errors="raise",
        )
    except (TypeError, ValueError, OverflowError):
        # Mixed/dirty declared columns remain exportable: invalid cells stay
        # text instead of being silently converted to NaT.
        return value
    if pd.isna(parsed):
        return None
    logical = str(raw_type or "").strip().lower()
    if logical == "date":
        return parsed.date()
    return parsed


def _xlsx_plain_cell_value(value: Any) -> Any:
    """Normalize one pandas/numpy value without logical-type coercion."""
    if value is None or value is pd.NA or value is pd.NaT:
        return None

    # Hot path: XLSX export visits this function once per cell.  Values
    # returned by ``DataFrame.itertuples`` and database drivers are normally
    # exact Python scalars, so handle those before the more general (and much
    # more expensive) isinstance chain below.
    value_type = type(value)
    if value_type is int or value_type is bool:
        return value
    if value_type is float:
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if value_type is str:
        return _clean_export_text(value)

    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        text = format(value.normalize(), "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"
    if isinstance(value, str):
        value = _clean_export_text(value)
    if isinstance(value, pd.Timestamp):
        value = value.to_pydatetime()
    elif isinstance(value, pd.Timedelta):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is not None and value.utcoffset() is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        # Excel's 1900 date system cannot round-trip these values reliably
        # (openpyxl may read 1899-12-31 back as midnight time). Preserve the
        # complete value as ISO text instead of rejecting or corrupting it.
        if value.year < 1900:
            return value.isoformat(sep=" ")
        return value
    if isinstance(value, date):
        return value.isoformat() if value.year < 1900 else value
    if isinstance(value, (time, timedelta, str, int, bool)):
        return value
    # pandas ExcelWriter converte gli object non nativi in testo. Manteniamo
    # lo stesso comportamento per dict/list/geometry e tipi applicativi.
    return str(value)


def _xlsx_temporal_cell_converter(raw_type: Any) -> Callable[[Any], Any]:
    """Compile temporal parsing once for a declared XLSX column."""

    def convert(value: Any) -> Any:
        if type(value) is str:
            value = _clean_export_text(value)
            value = _coerce_declared_excel_temporal(value, raw_type)
        return _xlsx_plain_cell_value(value)

    return convert


def _compile_xlsx_cell_converter(
    declared_type: Any = None,
) -> Callable[[Any], Any]:
    """Select one stable converter per column instead of branching per cell."""
    if declared_type and _is_declared_excel_temporal(declared_type):
        return _xlsx_temporal_cell_converter(declared_type)
    return _xlsx_plain_cell_value


def _xlsx_column_writer_kind(declared_type: Any = None, dtype: Any = None) -> int:
    """Return the cheapest safe XlsxWriter method for a known dataframe dtype."""
    if declared_type and _is_declared_excel_temporal(declared_type):
        return _XLSX_WRITE_GENERIC
    if dtype is None or pd.api.types.is_object_dtype(dtype):
        return _XLSX_WRITE_GENERIC
    if pd.api.types.is_bool_dtype(dtype):
        return _XLSX_WRITE_BOOLEAN
    if pd.api.types.is_integer_dtype(dtype):
        return _XLSX_WRITE_INTEGER
    if pd.api.types.is_float_dtype(dtype):
        return _XLSX_WRITE_FLOAT
    if pd.api.types.is_string_dtype(dtype):
        return _XLSX_WRITE_STRING
    return _XLSX_WRITE_GENERIC


def _write_typed_xlsx_row(
    worksheet: Any,
    row_number: int,
    row: Sequence[Any],
    converters: Sequence[Callable[[Any], Any]],
    writer_kinds: Sequence[int],
) -> int:
    """Write a dataframe row without repeating XlsxWriter type dispatch."""
    write = worksheet.write
    write_number = worksheet.write_number
    write_boolean = worksheet.write_boolean
    write_string = worksheet.write_string
    for column_number, (raw_value, converter, writer_kind) in enumerate(
        zip(row, converters, writer_kinds)
    ):
        if writer_kind == _XLSX_WRITE_INTEGER:
            if raw_value is None or raw_value is pd.NA:
                continue
            value = raw_value if type(raw_value) is int else int(raw_value)
            status = write_number(row_number, column_number, value)
        elif writer_kind == _XLSX_WRITE_FLOAT:
            if raw_value is None or raw_value is pd.NA:
                continue
            value = raw_value if type(raw_value) is float else float(raw_value)
            if math.isnan(value):
                continue
            if math.isinf(value):
                status = write_string(
                    row_number,
                    column_number,
                    "inf" if value > 0 else "-inf",
                )
            else:
                status = write_number(row_number, column_number, value)
        elif writer_kind == _XLSX_WRITE_BOOLEAN:
            if raw_value is None or raw_value is pd.NA:
                continue
            value = raw_value if type(raw_value) is bool else bool(raw_value)
            status = write_boolean(row_number, column_number, value)
        elif writer_kind == _XLSX_WRITE_STRING and type(raw_value) is str:
            status = write_string(
                row_number,
                column_number,
                _clean_export_text(raw_value),
            )
        else:
            value = converter(raw_value)
            # XlsxWriter's unformatted ``write(None)`` is a no-op as well.
            if value is None:
                continue
            status = write(row_number, column_number, value)
        if status != 0:
            return status
    return 0


def write_rows_to_xlsx_stream(
    rows: Iterable[Sequence[Any]],
    columns: Sequence[Any],
    sink: Any,
    *,
    sheet_name: str = "Sheet1",
    column_types: Mapping[str, Any] | None = None,
    column_dtypes: Sequence[Any] | None = None,
    row_count_hint: int | None = None,
) -> int:
    """Write tabular rows incrementally to a forward-only XLSX sink.

    ``constant_memory=True`` mantiene in RAM soltanto la riga corrente.
    Il sink puo' essere :class:`StreamingResultBlobWriter`, quindi il file ZIP
    viene inviato a S3 multipart senza un ``BytesIO`` completo.

    ``rows`` puo' essere un iteratore di tuple proveniente da pandas oppure
    direttamente da un cursore SQL. Il numero effettivo di righe scritte viene
    restituito al chiamante; ``row_count_hint`` consente il fail-fast quando il
    conteggio e' gia' noto.
    """
    import xlsxwriter

    column_count = len(columns)
    # Una riga e' riservata all'header, come ``DataFrame.to_excel(index=False)``.
    if row_count_hint is not None and row_count_hint + 1 > _XLSX_MAX_ROWS:
        raise ValueError(
            f"XLSX supporta al massimo {_XLSX_MAX_ROWS - 1} righe dati; "
            f"ricevute {row_count_hint}"
        )
    if column_count > _XLSX_MAX_COLUMNS:
        raise ValueError(
            f"XLSX supporta al massimo {_XLSX_MAX_COLUMNS} colonne; "
            f"ricevute {column_count}"
        )
    if column_dtypes is not None and len(column_dtypes) != column_count:
        raise ValueError(
            "Il numero dei dtype XLSX non coincide con quello delle colonne"
        )

    workbook = xlsxwriter.Workbook(
        sink,
        {
            "constant_memory": True,
            "in_memory": False,
            "strings_to_formulas": False,
            "strings_to_urls": False,
            "default_date_format": "yyyy-mm-dd hh:mm:ss",
        },
    )
    closed = False
    try:
        worksheet = workbook.add_worksheet(sheet_name)
        status = worksheet.write_row(
            0,
            0,
            [_xlsx_plain_cell_value(column) for column in columns],
        )
        if status != 0:
            raise ValueError(f"Impossibile scrivere l'intestazione XLSX (codice {status})")
        declared_by_lower = {
            str(name).lower(): declared_type
            for name, declared_type in (column_types or {}).items()
        }
        declared_types = [
            (column_types or {}).get(str(column))
            or declared_by_lower.get(str(column).lower())
            for column in columns
        ]
        dtypes = (
            column_dtypes
            if column_dtypes is not None
            else [None] * column_count
        )
        converters = [
            _compile_xlsx_cell_converter(declared_type)
            for declared_type in declared_types
        ]
        writer_kinds = [
            _xlsx_column_writer_kind(declared_type, dtype)
            for declared_type, dtype in zip(declared_types, dtypes)
        ]
        has_dataframe_dtypes = column_dtypes is not None
        row_count = 0
        for row_number, row in enumerate(rows, 1):
            if row_number >= _XLSX_MAX_ROWS:
                raise ValueError(
                    f"XLSX supporta al massimo {_XLSX_MAX_ROWS - 1} righe dati"
                )
            if has_dataframe_dtypes:
                status = _write_typed_xlsx_row(
                    worksheet,
                    row_number,
                    row,
                    converters,
                    writer_kinds,
                )
            else:
                status = worksheet.write_row(
                    row_number,
                    0,
                    [
                        converter(value)
                        for value, converter in zip(row, converters)
                    ],
                )
            if status != 0:
                raise ValueError(
                    f"Impossibile scrivere la riga XLSX {row_number + 1} "
                    f"(codice {status})"
                )
            row_count = row_number
        workbook.close()
        closed = True
        return row_count
    finally:
        if not closed:
            with suppress(Exception):
                workbook.close()


def write_dataframe_to_xlsx_stream(
    df: Any,
    sink: Any,
    *,
    sheet_name: str = "Sheet1",
    column_types: Mapping[str, Any] | None = None,
) -> None:
    """Write a DataFrame through the native accelerator or Python fallback."""
    from app.core.io.native_xlsx import (
        try_write_dataframe_to_xlsx_stream,
    )

    if try_write_dataframe_to_xlsx_stream(
        df,
        sink,
        sheet_name=sheet_name,
        column_types=column_types,
    ):
        return
    write_rows_to_xlsx_stream(
        df.itertuples(index=False, name=None),
        list(df.columns),
        sink,
        sheet_name=sheet_name,
        column_types=column_types,
        column_dtypes=tuple(df.dtypes),
        row_count_hint=len(df),
    )


def _records_json_inner(df_slice: Any) -> str:
    """Serializza uno slice in items JSON canonici SENZA le parentesi quadre.

    Profilo data-safe unico (convergenza serializzazione): record sanitizzati
    (`sanitize_for_json`) + `json.dumps(default=str)` -> date ISO (non epoch-ms),
    NaN->null, numpy/Decimal/shapely normalizzati. Applicato per-batch così lo
    streaming resta RAM-bounded e l'output coincide col path bytes.
    """
    compact = normalize_object_decimals(df_slice)
    records = sanitize_for_json(compact.to_dict(orient="records"))
    body = json.dumps(records, default=str, ensure_ascii=False)
    # `records` -> sempre un array JSON `[...]`; restituiamo l'interno.
    return body[1:-1] if body.startswith("[") and body.endswith("]") else body
_DEFAULT_QUEUE_MAXSIZE = 4  # bounded buffer: ~4 * chunksize rows in flight
_SENTINEL = object()


def _to_csv_minimal_copy(df: Any, buf: io.BytesIO, chunksize: int) -> None:
    """CSV: pandas `to_csv(text_buf)` scrive direttamente sul wrapped
    text-mode senza materializzare la stringa Python intera. Il
    `chunksize` divide la serializzazione interna di pandas in batch
    da N righe, riducendo il peak intermedio."""
    text_buf = io.TextIOWrapper(
        buf, encoding="utf-8", write_through=True, newline="",
    )
    try:
        # float_format="%.15g": precisione piena + compatta i whole-float
        # (5.0 -> "5"), profilo CSV canonico condiviso col path bytes.
        if len(df) == 0:
            df.to_csv(text_buf, index=False, encoding="utf-8")
        else:
            for start in range(0, len(df), chunksize):
                chunk = normalize_object_decimals(df.iloc[start:start + chunksize])
                chunk = _clean_export_text_chunk(chunk)
                chunk.to_csv(
                    text_buf,
                    index=False,
                    header=start == 0,
                    encoding="utf-8",
                    float_format="%.15g",
                )
        text_buf.flush()
    finally:
        # `detach()` riporta ownership del BytesIO al caller senza
        # chiuderlo, e cancella la reference TextIOWrapper.
        text_buf.detach()


def _to_json_minimal_copy(df: Any, buf: io.BytesIO) -> None:
    """JSON records: pandas `to_json(text_buf)` scrive sul wrapped
    text-mode. NB: `to_json` non supporta `chunksize` (a differenza
    di `to_csv`), quindi il peak della serializzazione interna resta
    O(df). Eliminata pero' la copia stringa intermedia di `to_json()`
    + `.encode('utf-8')`."""
    text_buf = io.TextIOWrapper(
        buf, encoding="utf-8", write_through=True, newline="",
    )
    try:
        # Profilo JSON canonico data-safe (vedi _records_json_inner): record
        # sanitizzati + json.dumps(default=str). Date ISO, NaN->null.
        text_buf.write("[")
        text_buf.write(_records_json_inner(df))
        text_buf.write("]")
        text_buf.flush()
    finally:
        text_buf.detach()


def dataframe_to_export_bytes(
    df: Any,
    dest_format: str,
    *,
    chunksize: int = _DEFAULT_CHUNKSIZE,
    column_types: Mapping[str, Any] | None = None,
) -> bytes:
    """Serializza `df` nel formato richiesto, ritorna i bytes.

    Path canonical per il task `CARICA` (output verso DataSource).
    Pattern minimal-copy: scrive direttamente sul `BytesIO` interno
    via wrapper text-mode, evitando la stringa Python intermedia di
    `df.to_csv().encode()`.

    Args:
        df: DataFrame pandas (o GeoDataFrame; il geojson branch ha la
            stessa semantica di to_json records).
        dest_format: 'csv' | 'xlsx' | legacy alias 'xls' | 'json' | 'geojson'.
            Default per format sconosciuto: csv.
        chunksize: numero di righe per batch nella serializzazione
            CSV interna pandas (default 10_000). Riduce il peak
            della string-building intermedia.

    Returns:
        `bytes` del payload pronto per `DataSourceService.write_file()`.
    """
    fmt = normalize_export_format(dest_format)
    buf = io.BytesIO()

    if fmt == "xlsx":
        # Unico path Excel: stesse policy per bytes e streaming (timezone,
        # date dichiarate e valori anteriori al 1900).
        write_dataframe_to_xlsx_stream(df, buf, column_types=column_types)
    elif fmt == "parquet":
        # engine="pyarrow" esplicito: gestisce Int64/Float64 nullable
        # nativamente (no degrado a float64 nel round-trip).
        df.to_parquet(buf, index=False, engine="pyarrow")
    elif fmt == "json" or fmt == "geojson":
        # Profilo JSON canonico data-safe (record sanitizzati + ISO date),
        # condiviso col path streaming via _records_json_inner.
        _to_json_minimal_copy(df, buf)
    else:
        # CSV come default + per fmt='csv' esplicito.
        _to_csv_minimal_copy(df, buf, chunksize)

    return buf.getvalue()


class _QueueBuf(io.RawIOBase):
    """File-like binary write-only che pubblica `write()` su una queue.

    Pensato come bridge tra un writer SINCRONO blocking (pandas
    `to_csv`/`to_json`) e un consumer ITERATIVO (`write_chunks`).
    Il writer chiama `write(bytes)` per ogni batch — i bytes vengono
    messi in coda; il consumer (in altro thread) li yielda al caller.

    Eredita da `io.RawIOBase` per compatibilita' con `io.TextIOWrapper`
    (richiede `readable/writable/seekable` + altre API del file-protocol).
    """

    def __init__(self, q: queue.Queue) -> None:
        super().__init__()
        self._q = q
        self._pos = 0

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def write(self, data) -> int:
        if isinstance(data, str):
            data = data.encode("utf-8")
        n = len(data)
        if n > 0:
            self._q.put(bytes(data))
            self._pos += n
        return n

    def flush(self) -> None:
        # No-op: la queue e' gia' eager push.
        pass

    def tell(self) -> int:
        return self._pos


def _csv_producer(
    df: Any, qbuf: _QueueBuf, chunksize: int, err_holder: list,
) -> None:
    try:
        text_buffer = io.TextIOWrapper(
            qbuf, encoding="utf-8", write_through=True, newline="",
        )
        try:
            if len(df) == 0:
                df.to_csv(text_buffer, index=False, encoding="utf-8")
            else:
                for start in range(0, len(df), chunksize):
                    chunk = normalize_object_decimals(df.iloc[start:start + chunksize])
                    chunk = _clean_export_text_chunk(chunk)
                    chunk.to_csv(
                        text_buffer,
                        index=False,
                        header=start == 0,
                        encoding="utf-8",
                        float_format="%.15g",
                    )
            text_buffer.flush()
        finally:
            text_buffer.detach()
    except Exception as exc:
        err_holder.append(exc)
    finally:
        qbuf._q.put(_SENTINEL)


def _json_producer(df: Any, qbuf: _QueueBuf, err_holder: list) -> None:
    """JSON records streaming manual record-by-record.

    Serializza record-by-record via `json.dumps` su batch
    da `_JSON_RECORDS_BATCH` righe. Peak teorico: 1 batch DataFrame
    + bytes serializzati di quel batch. Per file molto grandi
    significa centinaia di MB invece di GB.

    Output equivalente a `df.to_json(orient='records', force_ascii=False)`:
    array JSON `[{...}, {...}, ...]` con encoding UTF-8 nativo.

    Per dtypes pandas extension (Int64 nullable, datetime64,
    Categorical, ecc.) usa `record.to_json()` se disponibile via
    intermediate DataFrame slice, fallback `df.iloc[start:end].to_json`
    per batch."""
    _JSON_RECORDS_BATCH = 5_000

    try:
        text_buffer = io.TextIOWrapper(
            qbuf, encoding="utf-8", write_through=True, newline="",
        )
        try:
            n = len(df)
            text_buffer.write("[")
            first = True
            for start in range(0, n, _JSON_RECORDS_BATCH):
                end = min(start + _JSON_RECORDS_BATCH, n)
                # `to_json(orient='records')` su slice -> stringa JSON
                # array (output a stringa, no path filesystem).
                # NB: chiamata single-line per match `_BUFFER_HINTS`
                # regex del boundary gate (kwarg-only -> classificato
                # come buffer-write, non path-write).
                batch_slice = df.iloc[start:end]
                # Profilo JSON canonico data-safe per-batch (RAM-bounded):
                # record sanitizzati + json.dumps(default=str). Date ISO.
                inner = _records_json_inner(batch_slice)
                if not inner:
                    continue
                if first:
                    text_buffer.write(inner)
                    first = False
                else:
                    text_buffer.write(",")
                    text_buffer.write(inner)
            text_buffer.write("]")
            text_buffer.flush()
        finally:
            text_buffer.detach()
    except Exception as exc:
        err_holder.append(exc)
    finally:
        qbuf._q.put(_SENTINEL)


def _parquet_producer(df: Any, qbuf: _QueueBuf, chunksize: int, err_holder: list) -> None:
    """Parquet streaming row-group via pyarrow.ParquetWriter.

    Pyarrow ParquetWriter scrive row groups forward + footer al close
    (formato append-only, no seek required sul sink). Iteriamo batches
    da `chunksize` righe via `pa.RecordBatch.from_pandas(df.iloc[s:e])`,
    schema preso dal primo batch. Peak teorico: 2 batches in flight
    + queue buffer.
    """
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq

        n = len(df)
        if n == 0:
            # DataFrame vuoto: scrivi un file parquet con schema-only.
            empty_table = pa.Table.from_pandas(df, preserve_index=False)
            writer = pq.ParquetWriter(qbuf, empty_table.schema)
            writer.close()
            return

        # Schema inferito su sample piu' largo per evitare ArrowInvalid
        # quando il primo batch ha tutte None su colonne object e i batch
        # successivi contengono stringhe. Minimo 1000 righe copre la
        # maggior parte dei pattern; il caso patologico
        # (10k+ righe iniziali tutte None) resta possibile ma viene
        # rilanciato pulito come exception nel producer.
        sample_size = min(n, max(chunksize, 1000))
        sample_table = pa.Table.from_pandas(
            df.iloc[:sample_size], preserve_index=False,
        )
        writer = pq.ParquetWriter(qbuf, sample_table.schema)
        try:
            writer.write_table(sample_table)
            for start in range(sample_size, n, chunksize):
                end = min(start + chunksize, n)
                # `schema=` forza coerenza cross-batch via cast pyarrow:
                # se un batch successivo ha dtypes incompatibili viene
                # rilanciato ArrowInvalid invece di silently writing
                # un row group con schema diverso (file corrotto).
                batch_table = pa.Table.from_pandas(
                    df.iloc[start:end],
                    preserve_index=False,
                    schema=sample_table.schema,
                )
                writer.write_table(batch_table)
        finally:
            writer.close()  # flush footer
    except Exception as exc:
        err_holder.append(exc)
    finally:
        qbuf._q.put(_SENTINEL)


def dataframe_to_export_chunks(
    df: Any,
    dest_format: str,
    *,
    chunksize: int = _DEFAULT_CHUNKSIZE,
    column_types: Mapping[str, Any] | None = None,
) -> Iterator[bytes]:
    """Streaming export: yielda chunks invece di materializzare bytes.

    Path canonical per il task `CARICA` quando serve davvero RAM bounded
    (DataFrame >> RAM disponibile). Producer thread chiama il pandas
    writer su `_QueueBuf`; consumer (questo generator) yielda i bytes
    dalla queue.

    Peak RAM teorico: `chunksize * row_size * QUEUE_MAXSIZE` invece di
    sum(serialized_rows). Per default (chunksize=10k, maxsize=4): ~40k
    righe in flight invece di tutto il DF serializzato.

    Args:
        df: DataFrame pandas (o GeoDataFrame; geojson branch e'
            sintatticamente identico a json records).
        dest_format: 'csv' | 'xlsx' | legacy alias 'xls' | 'json' | 'geojson'.
        chunksize: righe per batch nella serializzazione CSV pandas.
            Ignorato per JSON (pandas to_json non supporta chunksize)
            e XLSX (contratto generator a chunk singolo).

    Yields:
        `bytes` chunks. L'ultimo chunk puo' essere piu' piccolo o vuoto.

    Raises:
        Exception sollevata dal pandas writer nel producer thread
        viene rilanciata al consumer dopo il sentinel.

    Note XLSX: questo generator mantiene il vecchio contratto bytes per i
    caller generici. Il path canonical dei result usa invece
    ``write_dataframe_to_xlsx_stream`` direttamente sul writer multipart.
    """
    fmt = normalize_export_format(dest_format)

    if fmt == "xlsx":
        # Il contratto generator generico resta a chunk singolo; il path
        # result usa write_dataframe_to_xlsx_stream sul sink multipart.
        yield dataframe_to_export_bytes(
            df,
            fmt,
            column_types=column_types,
        )
        return

    q: queue.Queue = queue.Queue(maxsize=_DEFAULT_QUEUE_MAXSIZE)
    qbuf = _QueueBuf(q)
    err_holder: list = []

    if fmt in ("csv", ""):
        target = _csv_producer
        args = (df, qbuf, chunksize, err_holder)
    elif fmt in ("json", "geojson"):
        target = _json_producer
        args = (df, qbuf, err_holder)
    elif fmt == "parquet":
        # Parquet row-group streaming via pyarrow ParquetWriter. Append-only
        # writer + footer al close, no seek required sul sink.
        target = _parquet_producer
        args = (df, qbuf, chunksize, err_holder)
    else:
        # Format sconosciuto: fallback CSV come dataframe_to_export_bytes.
        target = _csv_producer
        args = (df, qbuf, chunksize, err_holder)

    t = threading.Thread(target=target, args=args, daemon=True)
    t.start()
    try:
        while True:
            item = q.get()
            if item is _SENTINEL:
                break
            yield item
    finally:
        # Drena la queue per sbloccare il producer se il consumer abbandona
        # il generator mentre il thread e' fermo su `q.put()`.
        while t.is_alive():
            try:
                q.get(timeout=0.1)
            except queue.Empty:
                pass
        t.join(timeout=30.0)

    if err_holder:
        # Producer ha sollevato: rilancia la stessa exception.
        raise err_holder[0]


__all__ = [
    "dataframe_to_export_bytes",
    "dataframe_to_export_chunks",
    "normalize_export_format",
    "write_dataframe_to_xlsx_stream",
    "write_rows_to_xlsx_stream",
]
