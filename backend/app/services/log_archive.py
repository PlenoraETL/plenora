"""
log_archive.py — writer isolato PG-agnostic per archive+purge di
`plenora.logs` e `plenora.audit_actions` (O-ARC Batch 1, ADR-0014).

Perimetro Batch 1
=================
Modulo puro, non importato da alcun file di produzione. Hook beat,
env var (`LOG_ARCHIVE_*`, `AUDIT_RETENTION_DAYS`), estensione di
`validate_storage_config()` e runbook arrivano nei batch successivi:

- Batch 2: env var + fail-fast check a boot.
- Batch 3: chiamata dal beat, gate retention su archive success.
- Batch 4: integration test + runbook ops.

Contratto
=========
- Storage via Protocol `ObjectStorage` (ADR-0007), costruttore injection.
- SELECT e DELETE iniettate come callables dal chiamante (beat in Batch 3).
- Il bypass PG `SET LOCAL plenora.retention_cleanup = 'true'` NON vive qui:
  resta scoped al chiamante autorizzato (un solo titolare = beat job).
- datetime naive -> `ValueError` (fail-fast su tz upstream).
- Verifica post-upload via `list_objects(prefix=key)`: se la key non
  risulta presente con `size > 0`, la DELETE non viene mai invocata.

Riferimenti
===========
- ADR-0014: backend/docs/architecture/decisions/0014-2026-04-21-log-archive-pipeline.md
- Design:   orchestrate/phase_2_hardening/o_arc_archive_pipeline.md §4, §5
- Triage:   orchestrate/phase_2_hardening/o_arc_batch1_triage.md §3, §6
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from typing import Callable, Literal, TypedDict

from app.shared.cloud_backends import ObjectStorage


# ---------------------------------------------------------------------------
# Canonical NDJSON shapes (O-ARC §5.1)
# ---------------------------------------------------------------------------
# Ordine vincolante: garantisce output deterministico byte-level in
# combinazione con gzip(mtime=0). Un'eventuale aggiunta di colonna al
# DB (migration futura) richiede aggiornamento coordinato qui + ADR.

_LOGS_FIELDS: tuple[str, ...] = (
    "id", "ts", "level", "module", "op", "msg",
    "project_id", "group_id", "run_id", "node_id",
    "details", "error",
)

_AUDIT_FIELDS: tuple[str, ...] = (
    "id", "timestamp", "user_id", "action", "target",
    "status", "ip_address", "error_message", "metadata",
)

_FIELDS_BY_TABLE: dict[str, tuple[str, ...]] = {
    "logs": _LOGS_FIELDS,
    "audit_actions": _AUDIT_FIELDS,
}

_TS_COLUMN_BY_TABLE: dict[str, str] = {
    "logs": "ts",
    "audit_actions": "timestamp",
}


# ---------------------------------------------------------------------------
# Helpers (private) — serializzazione NDJSON gzip
# ---------------------------------------------------------------------------


def _table_ts_column(table: str) -> str:
    """Ritorna il nome della colonna temporale per `table`.

    `logs -> "ts"`, `audit_actions -> "timestamp"` (O-ARC assunzione #4).
    Solleva `ValueError` per tabelle sconosciute: il `Literal[...]` sulla
    firma pubblica limita il typing-time, questo check chiude il runtime.
    """
    try:
        return _TS_COLUMN_BY_TABLE[table]
    except KeyError as exc:
        raise ValueError(
            f"unknown log archive table {table!r}; expected one of "
            f"{sorted(_TS_COLUMN_BY_TABLE)}"
        ) from exc


def _normalize_datetime(value: datetime) -> str:
    """Normalizza un datetime tz-aware in ISO 8601 UTC con suffisso `Z`.

    - Input naive (`tzinfo is None`) -> `ValueError` (ADR-0014 §5).
    - Input non-UTC -> convertito in UTC.
    - Microsecondi preservati se presenti.
    """
    if not isinstance(value, datetime):
        raise TypeError(
            f"_normalize_datetime expected datetime, got {type(value).__name__}"
        )
    if value.tzinfo is None:
        raise ValueError(
            f"naive datetime is not allowed in log archive rows: {value!r}"
        )
    utc_value = value.astimezone(timezone.utc)
    # isoformat() su un datetime UTC produce "...+00:00"; il contratto
    # O-ARC §5.1 prescrive il suffisso `Z`.
    return utc_value.isoformat().replace("+00:00", "Z")


def _json_default(obj: object) -> str:
    """Encoder di fallback per `json.dumps`.

    Gestisce i `datetime` (incluso il rifiuto fail-fast dei naive) e
    propaga `TypeError` per ogni altro tipo non JSON-serializable. Non
    distingue tra la colonna ts/timestamp e campi datetime eventualmente
    annidati in `details`/`metadata`: naive = bug upstream in ogni caso.
    """
    if isinstance(obj, datetime):
        return _normalize_datetime(obj)
    raise TypeError(
        f"log archive row contains non-serializable value "
        f"of type {type(obj).__name__}"
    )


def _serialize_rows(rows: list[dict], table: str) -> bytes:
    """Serializza un batch di righe in NDJSON UTF-8 compresso gzip.

    Shape (O-ARC §5.1): una riga NDJSON per riga DB, keyset canonico per
    `table`, campi extra dell'input vengono droppati, campi canonici
    assenti vengono emessi come `null`. JSON compatto senza spazi.

    Invarianti:
    - `gzip.compresslevel=6` (O-ARC §5.1 "livello default (6)").
    - `gzip.mtime=0` (byte-level determinism per re-run idempotency,
      O-ARC §4.4 "stessi byte").
    - `separators=(",", ":")` (no whitespace).
    - Trailing newline dopo l'ultima riga NDJSON.

    Solleva `ValueError` se `table` non e' riconosciuta o se una riga
    contiene datetime naive. Propaga `TypeError` per valori non
    JSON-serializable (via `_json_default`).
    """
    _table_ts_column(table)  # validazione runtime di `table`
    fields = _FIELDS_BY_TABLE[table]

    lines: list[str] = []
    for row in rows:
        canonical = {field: row.get(field) for field in fields}
        lines.append(
            json.dumps(
                canonical,
                separators=(",", ":"),
                ensure_ascii=False,
                default=_json_default,
            )
        )

    payload = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    return gzip.compress(payload, compresslevel=6, mtime=0)


# ---------------------------------------------------------------------------
# Helpers (private) — object key naming (O-ARC §5.2)
# ---------------------------------------------------------------------------
# Pattern deterministico:
#   <prefix><table>/<YYYY-MM>/<table>-<YYYY-MM-DD>-<NNN>.ndjson.gz
#
# - Il partitioning YYYY-MM / YYYY-MM-DD e' derivato dal `run_date`
#   convertito in UTC (coerente con la normalizzazione `Z` del payload).
# - `seq` e' zero-padded a 3 cifre (cap 999): ordinamento lessicografico
#   delle key rispecchia l'ordine di emissione all'interno dello stesso
#   giorno. Il cap 999 e' un invariante del pattern, non un limite di
#   policy: superarlo significa che il sizing batch a monte e' mal tarato
#   e va alzato via `LOG_ARCHIVE_BATCH_ROWS` (Batch 2) piuttosto che
#   estendere il padding.

_MAX_BATCH_SEQ: int = 999


def _build_object_key(
    prefix: str,
    table: str,
    run_date: datetime,
    seq: int,
) -> str:
    """Compone la chiave object storage per un batch (O-ARC §5.2).

    Pattern:
        ``<prefix><table>/<YYYY-MM>/<table>-<YYYY-MM-DD>-<NNN>.ndjson.gz``

    Invarianti:
    - `prefix` deve essere stringa vuota o terminare con `"/"`: altrimenti
      la concatenazione produce un path ambiguo.
    - `table` validata tramite `_table_ts_column`.
    - `run_date` tz-aware: naive -> `ValueError` (coerente con
      `_normalize_datetime`). La componente UTC e' usata per derivare
      YYYY-MM e YYYY-MM-DD.
    - `seq` intero in `[0, _MAX_BATCH_SEQ]`: out-of-range -> `ValueError`.

    La funzione e' pura: stessi input -> stesso output, byte per byte.
    """
    if prefix != "" and not prefix.endswith("/"):
        raise ValueError(
            f"log archive key prefix must be empty or end with '/': {prefix!r}"
        )
    _table_ts_column(table)  # validazione runtime di `table`
    if not isinstance(run_date, datetime):
        raise TypeError(
            f"_build_object_key expected datetime for run_date, "
            f"got {type(run_date).__name__}"
        )
    if run_date.tzinfo is None:
        raise ValueError(
            f"naive datetime is not allowed as run_date: {run_date!r}"
        )
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise TypeError(
            f"_build_object_key expected int for seq, "
            f"got {type(seq).__name__}"
        )
    if seq < 0 or seq > _MAX_BATCH_SEQ:
        raise ValueError(
            f"batch seq out of range [0, {_MAX_BATCH_SEQ}]: {seq}"
        )

    utc_date = run_date.astimezone(timezone.utc)
    month = utc_date.strftime("%Y-%m")
    day = utc_date.strftime("%Y-%m-%d")
    return f"{prefix}{table}/{month}/{table}-{day}-{seq:03d}.ndjson.gz"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class ArchiveResult(TypedDict):
    """Esito cumulativo di un ciclo `archive_and_purge`.

    - `archived_rows`: conteggio totale righe archiviate+purgate nel run.
    - `object_keys`: chiavi object storage create/sovrascritte, in ordine di upload.
    - `cutoff_ts`: timestamp limite usato dalla SELECT (`< cutoff_ts` -> archive+delete).
    """

    archived_rows: int
    object_keys: list[str]
    cutoff_ts: datetime


class LogArchiveWriter:
    """Writer storage-agnostic per archive+purge di `plenora.logs` e
    `plenora.audit_actions`.

    L'istanza e' riutilizzabile tra run: non tiene stato mutabile oltre
    ai parametri ricevuti dal costruttore. In Batch 1 il writer non ha
    chiamanti di produzione; il costruttore e' modellato per essere
    invocato dal beat in Batch 3 tramite closure che incapsula le
    transazioni PG autorizzate.
    """

    def __init__(
        self,
        storage: ObjectStorage,
        key_prefix: str,
        batch_size: int,
    ) -> None:
        """
        Parametri
        ---------
        storage
            Implementazione di `ObjectStorage` (es. `AwsS3ObjectStorage`;
            futura `MinioObjectStorage` per on-prem tramite track A-A1b).
        key_prefix
            Prefisso deterministico delle chiavi object storage, es.
            `"logs-archive/prod/"`. Attesa terminazione con `"/"` per
            comporre il path finale `<prefix><table>/<YYYY-MM>/...`
            (O-ARC §5.2). La validazione del formato e' responsabilita'
            del chiamante.
        batch_size
            Numero massimo di righe per iterazione SELECT → serialize →
            upload → DELETE. E' anche il cap pratico di RAM per batch.
        """
        self._storage = storage
        self._key_prefix = key_prefix
        self._batch_size = batch_size

    def archive_and_purge(
        self,
        table: Literal["logs", "audit_actions"],
        cutoff_ts: datetime,
        select_rows: Callable[[datetime, int], list[dict]],
        retention_bypass_execute: Callable[[str, tuple], None],
        run_date: datetime | None = None,
    ) -> ArchiveResult:
        """
        Archivia su object storage e purga dal DB le righe con
        `<ts_column> < cutoff_ts`, dove `ts_column` e' `ts` per `logs` e
        `timestamp` per `audit_actions` (O-ARC assunzione #4).

        Contratto per iterazione batch (ADR-0014 §7):
          SELECT callable -> serialize NDJSON gzip ->
          `storage.upload_bytes(data, key)` -> verifica via
          `storage.list_objects(prefix=key)` -> DELETE scoped
          `AND id <= max(batch_ids)` via callable.

        Itera finche' `select_rows(cutoff_ts, batch_size)` ritorna `[]`.

        Parametri
        ---------
        table
            `"logs"` (colonna temporale `ts`) oppure `"audit_actions"`
            (colonna `timestamp`).
        cutoff_ts
            Datetime tz-aware. Un datetime naive (tzinfo=None) solleva
            `ValueError`.
        select_rows
            Callable `(cutoff_ts, batch_size) -> list[dict]`. Il
            chiamante e' responsabile dell'ordinamento stabile
            `ORDER BY ts ASC, id ASC LIMIT batch_size` e dell'apertura
            eventuale di una transazione di sola lettura.
        retention_bypass_execute
            Callable `(sql, params) -> None`. Il chiamante apre la tx
            con `SET LOCAL plenora.retention_cleanup = 'true'` **prima**
            di eseguire, mantenendo scoped il bypass del trigger
            `plenora.block_log_modifications`.
        run_date
            Data di partitioning dell'object key (pattern
            `<table>/<YYYY-MM>/<table>-<YYYY-MM-DD>-<NNN>.ndjson.gz`,
            O-ARC §5.2). Default: `datetime.now(tz=UTC)`.

        Ritorna
        -------
        `ArchiveResult` con conteggio cumulativo e chiavi create/sovrascritte.

        Solleva
        -------
        ValueError
            Se `cutoff_ts` e' naive, o se una riga contiene datetime naive.
        RuntimeError
            Se dopo `upload_bytes` la verifica via `list_objects` non
            trova la key con `size > 0`. La DELETE non viene invocata.
        Exception
            Qualsiasi eccezione da `upload_bytes` o dalla callable DELETE
            propaga senza swallow.
        """
        # ---- fail-fast validation (prima di qualsiasi side-effect) ------
        if not isinstance(cutoff_ts, datetime):
            raise TypeError(
                f"archive_and_purge expected datetime for cutoff_ts, "
                f"got {type(cutoff_ts).__name__}"
            )
        if cutoff_ts.tzinfo is None:
            raise ValueError(
                f"naive datetime is not allowed as cutoff_ts: {cutoff_ts!r}"
            )

        ts_column = _table_ts_column(table)
        effective_run_date = (
            run_date if run_date is not None else datetime.now(tz=timezone.utc)
        )
        # Dry-run su `_build_object_key` con seq=0: valida in un colpo solo
        # prefix, table e run_date PRIMA di invocare `select_rows`. Se
        # `run_date` e' naive o `key_prefix` e' mal formato, l'errore esce
        # qui, non a meta' iterazione.
        _build_object_key(self._key_prefix, table, effective_run_date, 0)

        # ---- loop SELECT -> serialize -> upload -> verify -> DELETE -----
        archived_rows_total = 0
        object_keys: list[str] = []
        seq = 0

        while True:
            rows = select_rows(cutoff_ts, self._batch_size)
            if not rows:
                break

            payload = _serialize_rows(rows, table)
            key = _build_object_key(
                self._key_prefix, table, effective_run_date, seq
            )

            self._storage.upload_bytes(payload, key)

            # Unico meccanismo di verify: `list_objects(prefix=key)` deve
            # ritornare almeno un `ObjectMeta` con `key` esattamente pari
            # e `size > 0`. Nessun HEAD, nessun checksum: il Protocol
            # non viene esteso in Batch 1 (ADR-0014 §Decision 7).
            listed = self._storage.list_objects(prefix=key)
            if not any(
                obj["key"] == key and obj["size"] > 0 for obj in listed
            ):
                raise RuntimeError(
                    f"log archive verify failed: key {key!r} not visible "
                    f"via list_objects(prefix=key) with size > 0 after "
                    f"upload_bytes. No DELETE will be executed on "
                    f"{table!r}. Rows remain in DB; the next run will "
                    f"retry with the same deterministic key."
                )

            # DELETE scoped: `<ts_col> < cutoff_ts AND id <= max(batch_ids)`.
            # Il bound su `id` garantisce che non cancelliamo righe inserite
            # nella stessa finestra temporale da tx concorrenti (append-only
            # su id crescente -> id fuori dal batch = riga piu' recente).
            max_id = max(row["id"] for row in rows)
            sql = (
                f"DELETE FROM plenora.{table} "
                f"WHERE {ts_column} < %s AND id <= %s"
            )
            retention_bypass_execute(sql, (cutoff_ts, max_id))

            archived_rows_total += len(rows)
            object_keys.append(key)
            seq += 1

        return ArchiveResult(
            archived_rows=archived_rows_total,
            object_keys=object_keys,
            cutoff_ts=cutoff_ts,
        )
