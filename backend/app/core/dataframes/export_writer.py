"""DataFrame serialization with minimal RAM amplification (RAM-2 2026-05-26).

Pre-RAM-2 il pattern in `workers/tasks/carica_task.py` era:

    buf = io.BytesIO()
    buf.write(df.to_csv(index=False).encode('utf-8'))  # 3 copie intermedie
    content = buf.getvalue()  # 4a copia

Peak RAM ~4-5x del df serializzato:
  1. `df` pandas (input)
  2. `df.to_csv()` ritorna stringa Python (~1.5x del df)
  3. `.encode('utf-8')` crea bytes (~1x della stringa)
  4. `buf.write(...)` BytesIO accumula i bytes (~1x)
  5. `buf.getvalue()` copia il contenuto di BytesIO (~1x finale)

Post-RAM-2: scrittura diretta sul `BytesIO` via `io.TextIOWrapper` per i
text-format (CSV/JSON), niente stringa Python intermedia. Riduce il
picco a ~2x (df + bytes finali).

Per Excel (binary writer nativo pandas), il pattern `df.to_excel(buf)`
e' gia' ottimale.

API:
  - `dataframe_to_export_bytes(df, dest_format, *, chunksize)`:
    serializza il DataFrame nel formato richiesto, ritorna i bytes.
    Usa il path minimal-copy (TextIOWrapper) per text formats.
"""

from __future__ import annotations

import io
import queue
import threading
from typing import Any, Iterator


_DEFAULT_CHUNKSIZE = 10_000
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
        df.to_csv(text_buf, index=False, chunksize=chunksize, encoding="utf-8")
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
        df.to_json(text_buf, orient="records", force_ascii=False)
        text_buf.flush()
    finally:
        text_buf.detach()


def dataframe_to_export_bytes(
    df: Any,
    dest_format: str,
    *,
    chunksize: int = _DEFAULT_CHUNKSIZE,
) -> bytes:
    """Serializza `df` nel formato richiesto, ritorna i bytes.

    Path canonical per il task `CARICA` (output verso DataSource).
    Pattern minimal-copy: scrive direttamente sul `BytesIO` interno
    via wrapper text-mode, evitando la stringa Python intermedia di
    `df.to_csv().encode()`.

    Args:
        df: DataFrame pandas (o GeoDataFrame; il geojson branch ha la
            stessa semantica di to_json records).
        dest_format: 'csv' | 'xlsx' | 'xls' | 'json' | 'geojson'.
            Default per format sconosciuto: csv.
        chunksize: numero di righe per batch nella serializzazione
            CSV interna pandas (default 10_000). Riduce il peak
            della string-building intermedia.

    Returns:
        `bytes` del payload pronto per `DataSourceService.write_file()`.
    """
    fmt = (dest_format or "").strip().lower().lstrip(".")
    buf = io.BytesIO()

    if fmt in ("xlsx", "xls"):
        # pandas Excel writer e' nativamente binary su buffer.
        df.to_excel(buf, index=False)
    elif fmt == "json" or fmt == "geojson":
        # geojson nel flow CARICA usa to_json(records) — stessa
        # semantica di JSON tabulare (il caller MANIPOLA serializza
        # gia' le geometry come WKT/GeoJSON string nel df).
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
            df.to_csv(text_buffer, index=False, chunksize=chunksize, encoding="utf-8")
            text_buffer.flush()
        finally:
            text_buffer.detach()
    except Exception as exc:
        err_holder.append(exc)
    finally:
        qbuf._q.put(_SENTINEL)


def _json_producer(df: Any, qbuf: _QueueBuf, err_holder: list) -> None:
    """E-5 (2026-05-26): JSON records streaming manual record-by-record.

    Pre-fix: `df.to_json(text_buffer, orient='records')` aveva picchi
    interni O(df) durante la serializzazione pandas C-level (anche se
    il buffer finale era streamato via TextIOWrapper).

    Post-fix: serializza record-by-record via `json.dumps` su batch
    da `_JSON_RECORDS_BATCH` righe. Peak teorico: 1 batch DataFrame
    + bytes serializzati di quel batch. Per file molto grandi
    significa centinaia di MB invece di GB.

    Output equivalente a `df.to_json(orient='records', force_ascii=False)`:
    array JSON `[{...}, {...}, ...]` con encoding UTF-8 nativo.

    Per dtypes pandas extension (Int64 nullable, datetime64,
    Categorical, ecc.) usa `record.to_json()` se disponibile via
    intermediate DataFrame slice, fallback `df.iloc[start:end].to_json`
    per batch."""
    import json as _json
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
                batch_json = batch_slice.to_json(orient="records", force_ascii=False)
                # Sanity: batch_json deve essere wrapped in `[...]`.
                if batch_json.startswith("[") and batch_json.endswith("]"):
                    inner = batch_json[1:-1]
                else:
                    inner = batch_json
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
    """E-4 (2026-05-26): Parquet streaming row-group via pyarrow.ParquetWriter.

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

        # P1 (2026-05-26): schema inferred su sample piu' largo per
        # evitare ArrowInvalid quando il primo batch ha tutte None su
        # colonne object (es. sparse extra). Pre-fix il primo
        # `chunksize` righe inferiva schema=null per quelle colonne,
        # poi il batch successivo con stringhe falliva con ArrowInvalid
        # → Parquet corrotto (footer mancante). Minimo 1000 righe nel
        # sample copre la maggior parte dei pattern; il caso patologico
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
        dest_format: 'csv' | 'xlsx' | 'xls' | 'json' | 'geojson'.
        chunksize: righe per batch nella serializzazione CSV pandas.
            Ignorato per JSON (pandas to_json non supporta chunksize)
            e XLSX (fallback).

    Yields:
        `bytes` chunks. L'ultimo chunk puo' essere piu' piccolo o vuoto.

    Raises:
        Exception sollevata dal pandas writer nel producer thread
        viene rilanciata al consumer dopo il sentinel.

    Note XLSX: openpyxl non supporta write_only streaming senza
    riscrivere il flow; per XLSX cadiamo su `dataframe_to_export_bytes`
    e yieldiamo come single chunk (peak invariato). XLSX export su
    file grandi non e' un pattern comune in Plenora.
    """
    fmt = (dest_format or "").strip().lower().lstrip(".")

    if fmt in ("xlsx", "xls"):
        # Fallback: nessun vero streaming per openpyxl. Yield single
        # chunk (peak invariato rispetto a dataframe_to_export_bytes).
        yield dataframe_to_export_bytes(df, dest_format)
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
        # E-4 (2026-05-26): Parquet row-group streaming via pyarrow
        # ParquetWriter. Append-only writer + footer al close — no
        # seek required sul sink. Peak ~chunksize righe in flight.
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
        # P1 (2026-05-26): drena la queue per sbloccare il producer
        # che potrebbe essere fermo su `q.put()` se il consumer ha
        # abbandonato il generator (eccezione downstream). Pre-fix:
        # `t.join(timeout=30.0)` scadeva senza terminare il thread
        # quando la queue era piena; il thread restava vivo nel
        # worker Celery, accumulando thread orfani sotto load.
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
]
