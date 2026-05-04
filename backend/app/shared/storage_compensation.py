"""Best-effort compensating-delete helper per object storage.

BE-GO-READY-STORAGE-ENGINE-002-COMPENSATING-DELETE.

Goal: unificare il pattern "upload-then-DB-orphan-delete" che oggi
vive in 4 callsite quasi identici (vedi `BE_STORAGE_ENGINE_AUDIT.md`
sezione 3.1):

  - `app.utils.result_store._cleanup_uploaded_result_blob`
    (compensating delete dopo `db.save_result(...)` fallita post-upload);
  - `app.db.results_db_service._s3_delete_best_effort`
    (cleanup S3 post-DELETE row);
  - `app.db.config.file_sources.delete_file_source`
    (cleanup post-commit con refcount-aware skip);
  - `app.services.file_service._delete_blob` callback consumato da
    `app.services.file_multi_layer_upload`
    (cleanup compensativo del shared blob su DB rollback).

Contract minimo di tutti i callsite:

  1. Mai sollevare verso il caller (il caller sta gia' propagando
     un errore root o ha appena committato/cancellato una row e
     non puo' permettersi di spostare l'orfano da S3 al DB).
  2. Capability `object_storage_enabled() = False` -> skip silente
     (non e' un errore, e' una configurazione legittima).
  3. Log strutturato per consentire alert/debug post-mortem
     (`event=storage_compensating_delete_{ok|failed|skipped}`).

Boundary
--------
Modulo puro al filo del cloud SDK: l'unica dipendenza esterna e'
`app.shared.cloud_backends` (gia' single-boundary verso boto3).
Nessun I/O DB, nessuna setting, nessuna validazione di policy. Il
caller fornisce la `domain` come tag testuale (`"result"`,
`"file_source"`, `"file_source_multi_layer"`, ...) e il
`storage_key` da cancellare; opzionalmente puo' iniettare
un'istanza `ObjectStorage` (es. `result_store` la ha gia' in mano
dal save successo) per evitare un secondo call a
`get_runtime_object_storage()`.

Go-ready mapping
----------------
    type CompensatingDelete func(
        ctx context.Context,
        domain string,
        storageKey string,
        opts CompensateOptions,
    ) (CompensateOutcome, error)

    type CompensateOptions struct {
        Storage  storage.Provider // optional, nil -> resolve via runtime factory
        Reason   string
        Extras   map[string]any
        Logger   *slog.Logger
    }

    type CompensateOutcome string  // "ok" | "failed" | "skipped"

Lo stato `outcome` e' utile in test e per metrics/observability;
in Python lo ritorniamo come literal string per evitare un Enum
dedicato (YAGNI: oggi nessun caller ramifica).
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Mapping, Optional

# Type alias public — caller possono lockare il valore di ritorno.
CompensateOutcome = Literal["ok", "failed", "skipped"]


__all__ = [
    "CompensateOutcome",
    "compensating_delete",
]


def _format_extras(extras: Optional[Mapping[str, Any]]) -> str:
    """Serializza `extras` in `key=value` separati da spazio per il
    log strutturato. Filtra le chiavi protette (`event`, `domain`,
    `storage_key`, `reason`, `outcome`, `cleanup_error`,
    `cleanup_error_msg`) per evitare che il caller sovrascriva
    accidentalmente i campi canonici.

    Valori `None` vengono saltati. Stringhe `repr`-ate per stabilita'
    (path con spazi, nomi con virgolette).
    """
    if not extras:
        return ""
    protected = {
        "event", "domain", "storage_key", "reason", "outcome",
        "cleanup_error", "cleanup_error_msg",
    }
    parts = []
    for k, v in extras.items():
        if k in protected or v is None:
            continue
        parts.append(f"{k}={v!r}")
    return (" " + " ".join(parts)) if parts else ""


def compensating_delete(
    storage_key: str,
    *,
    domain: str,
    reason: str,
    storage: Optional[Any] = None,
    extras: Optional[Mapping[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> CompensateOutcome:
    """Best-effort delete di `storage_key` su object storage.

    Contract:
      - mai solleva verso il caller (nemmeno su fail della delete);
      - se la capability `object_storage_enabled()` e' off, skip
        silente con outcome `"skipped"`;
      - se `storage` e' fornito (caller gia' ha l'istanza in mano,
        es. dopo un upload riuscito), usa quello e salta la
        risoluzione runtime; se `storage` e' `None`, risolve via
        `get_runtime_object_storage()`;
      - su `ObjectStorageDisabledError` durante la risoluzione (race
        capability-off fra dispatch e cleanup), tratta come
        skipped + log debug;
      - su qualsiasi altra eccezione durante la delete, log error
        strutturato `event=storage_compensating_delete_failed` con
        type+repr dell'errore + cleanup_error_msg, ritorna `"failed"`.

    Log shape (3 stati):

      event=storage_compensating_delete_ok        \\
        domain=<d> storage_key=<k> reason=<r> [extras...]
        (level=info)

      event=storage_compensating_delete_failed    \\
        domain=<d> storage_key=<k> reason=<r>     \\
        cleanup_error=<ExcType> cleanup_error_msg=<repr> [extras...]
        (level=warning — orphan accepted, ADR-0011 contract; non-fatal
        ai sensi del caller. Allerta osservabilita' tramite filtro su
        `event=storage_compensating_delete_failed` o domain.)

      event=storage_compensating_delete_skipped   \\
        domain=<d> storage_key=<k> reason=<r>     \\
        skip_reason=<capability_disabled|capability_disabled_race> [extras...]
        (level=debug — configurazione legittima, non e' un errore.)

    Args:
        storage_key: chiave object storage da rimuovere
            (es. `"results/<project>/<id>.bin"`).
        domain: tag del callsite per log filtering
            (`"result"`, `"file_source"`, `"file_source_multi_layer"`,
            `"result_blob"`, ...). Non interpretato; passato al log.
        reason: motivo della compensazione
            (`"db_save_failed_after_object_upload"`,
            `"row_delete_post_commit"`,
            `"batch_save_rollback"`, ...). Non interpretato; passato al log.
        storage: opzionale `ObjectStorage` instance. Se `None`,
            viene risolto via `get_runtime_object_storage()`.
        extras: campi opzionali per il log (`project_id`,
            `result_id`, `file_id`, `batch_id`, ...). Non interpretati.
        logger: logger custom. Se `None`, viene usato il logger del
            modulo `app.shared.storage_compensation` (caller perde
            la propria attribution, ma il log resta strutturato e
            filtrabile via `event=`/`domain=`).

    Returns:
        `"ok"` se delete riuscita;
        `"failed"` se delete tentata ma fallita;
        `"skipped"` se capability off (nessuna delete tentata).

    Raises:
        Mai. Anche se l'import di `cloud_backends` fallisse (modulo
        rotto), il fallback ritorna `"failed"` e logga.
    """
    log = logger or logging.getLogger(__name__)
    extras_suffix = _format_extras(extras)

    try:
        from app.shared.cloud_backends import (
            ObjectStorageDisabledError,
            get_runtime_object_storage,
            object_storage_enabled,
        )
    except Exception as imp_err:  # pragma: no cover — import failure is pathological
        log.warning(
            "event=storage_compensating_delete_failed "
            "domain=%s storage_key=%s reason=%s "
            "cleanup_error=%s cleanup_error_msg=%r%s",
            domain, storage_key, reason,
            type(imp_err).__name__, str(imp_err), extras_suffix,
        )
        return "failed"

    if not object_storage_enabled():
        log.debug(
            "event=storage_compensating_delete_skipped "
            "domain=%s storage_key=%s reason=%s "
            "skip_reason=capability_disabled%s",
            domain, storage_key, reason, extras_suffix,
        )
        return "skipped"

    target = storage
    if target is None:
        try:
            target = get_runtime_object_storage()
        except ObjectStorageDisabledError as e:
            # Race teorica: capability era ON al check sopra, OFF
            # qui. Trattiamo come skipped (non e' un fallimento di
            # delete: nulla da cancellare).
            log.debug(
                "event=storage_compensating_delete_skipped "
                "domain=%s storage_key=%s reason=%s "
                "skip_reason=capability_disabled_race "
                "skip_detail=%r%s",
                domain, storage_key, reason, str(e), extras_suffix,
            )
            return "skipped"
        except Exception as e:
            log.error(
                "event=storage_compensating_delete_failed "
                "domain=%s storage_key=%s reason=%s "
                "cleanup_error=%s cleanup_error_msg=%r%s",
                domain, storage_key, reason,
                type(e).__name__, str(e), extras_suffix,
            )
            return "failed"

    try:
        target.delete(storage_key)
    except Exception as e:
        log.warning(
            "event=storage_compensating_delete_failed "
            "domain=%s storage_key=%s reason=%s "
            "cleanup_error=%s cleanup_error_msg=%r%s",
            domain, storage_key, reason,
            type(e).__name__, str(e), extras_suffix,
        )
        return "failed"

    log.info(
        "event=storage_compensating_delete_ok "
        "domain=%s storage_key=%s reason=%s%s",
        domain, storage_key, reason, extras_suffix,
    )
    return "ok"
