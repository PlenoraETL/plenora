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

from datetime import datetime
from typing import Callable, Literal, TypedDict

from app.shared.cloud_backends import ObjectStorage


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
        raise NotImplementedError(
            "archive_and_purge() e' implementato nei commit 1b-1d di O-ARC Batch 1 (ADR-0014)"
        )
