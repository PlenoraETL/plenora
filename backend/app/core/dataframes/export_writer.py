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
from typing import Any


_DEFAULT_CHUNKSIZE = 10_000


def _to_csv_minimal_copy(df: Any, buf: io.BytesIO, chunksize: int) -> None:
    """CSV: pandas `to_csv(text_buf)` scrive direttamente sul wrapped
    text-mode senza materializzare la stringa Python intera. Il
    `chunksize` divide la serializzazione interna di pandas in batch
    da N righe, riducendo il peak intermedio."""
    text_buf = io.TextIOWrapper(
        buf, encoding="utf-8", write_through=True, newline="",
    )
    try:
        df.to_csv(text_buf, index=False, chunksize=chunksize)
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


__all__ = [
    "dataframe_to_export_bytes",
]
