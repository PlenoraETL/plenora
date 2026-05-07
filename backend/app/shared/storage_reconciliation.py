"""
Persistent reconciliation log for storage compensating-delete intent.

"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Mapping, Optional


logger = logging.getLogger(__name__)


ReconciliationOperation = Literal["delete", "upload_intent"]
ReconciliationState = Literal["pending", "done", "orphan_deleted", "lost"]


__all__ = [
    "ReconciliationOperation",
    "ReconciliationState",
    "ReconciliationEntryRef",
    "PendingReconciliationRow",
    "ReconciliationStateCount",
    "record_pending",
    "record_upload_intent",
    "mark_done",
    "mark_failed_attempt",
    "mark_orphan_deleted",
    "mark_lost",
    "fetch_due_pending_deletes",
    "fetch_due_stale_upload_intents",
    "is_storage_key_referenced",
    "compute_next_retry_at",
    "count_active_by_state_domain",
    "count_pending_by_operation",
    "prune_terminal_older_than",
]


@dataclass(frozen=True)
class ReconciliationEntryRef:
    """Tipato handle ritornato da `record_pending`.

    Identifica una riga in `plenora.storage_reconciliation_log` per
    le successive transizioni `mark_*`. Frozen perche' immutabile
    una volta committed.
    """

    id: str
    domain: str
    storage_key: str
    operation: ReconciliationOperation


@dataclass(frozen=True)
class PendingReconciliationRow:
    """Tipato snapshot di una riga `pending` ritornata dal reconciler.

     shape minima necessaria al beat
    hook per decidere cosa fare. NON e' un mirror completo della
    tabella; espone solo i campi che il reconciler legge:
      - `id`: per le transizioni `mark_*`;
      - `domain`/`storage_key`/`operation`: passati a
        `compensating_delete` (domain+storage_key) e usati per
        guardare l'invariante `operation == "delete"`;
      - `attempts`: per la decisione max-attempts → `lost`.

    Frozen, hashable: facile da comparare nei test, niente dict
    opaco cross-boundary.
    """

    id: str
    domain: str
    storage_key: str
    operation: ReconciliationOperation
    attempts: int


_INSERT_PENDING_SQL = (
    "INSERT INTO plenora.storage_reconciliation_log "
    "(domain, storage_key, operation, state, reason, extras) "
    "VALUES (%s, %s, %s, 'pending', %s, %s::jsonb) "
    "RETURNING id"
)


def record_pending(
    cursor,
    *,
    domain: str,
    storage_key: str,
    operation: ReconciliationOperation,
    reason: str,
    extras: Optional[Mapping[str, Any]] = None,
) -> ReconciliationEntryRef:
    """INSERT pending entry inside the caller's transaction.

    The caller MUST be inside a `transaction` context manager (or
    equivalent psycopg2 cursor). The INSERT shares the cursor's
    transaction so the commit/rollback is atomic with the caller's
    DB change.

    Args:
        cursor: psycopg2 cursor owned by the caller's transaction.
        domain: callsite tag ("file_source", "result_blob", ...).
            Not interpreted; persisted for filtering / dashboards.
        storage_key: object-storage key targeted by the future
            cleanup (e.g. "file-sources/<id>/<filename>").
        operation: "delete" today; "upload_intent" reserved for
            .
        reason: free-text reason persisted alongside the row
            ("row_delete_post_commit", ...). Not interpreted.
        extras: optional `Mapping[str, Any]` serialized to JSONB.
            Keep it small (debug metadata only); none of the fields
            are interpreted by the boundary or the future reconciler.

    Returns:
        `ReconciliationEntryRef` with the freshly-generated UUID
        plus echoes of `domain`, `storage_key`, `operation` for
        log correlation.

    Raises:
        Any psycopg2 / DB error from the underlying `cursor.execute`.
        Caller's transaction must rollback. Behavior intentional:
        if the reconciliation INSERT fails, persisting the DB DELETE
        without it would defeat the purpose of the log.
    """
    extras_json = json.dumps(dict(extras)) if extras else None
    cursor.execute(
        _INSERT_PENDING_SQL,
        (domain, storage_key, operation, reason, extras_json),
    )
    row = cursor.fetchone()
    new_id = row["id"] if isinstance(row, dict) else row[0]
    return ReconciliationEntryRef(
        id=str(new_id),
        domain=domain,
        storage_key=storage_key,
        operation=operation,
    )


def _autocommit_update(
    sql: str,
    params: tuple,
    *,
    reconciliation_id: str,
    transition: str,
) -> None:
    """Autocommit UPDATE shared by all `mark_*` helpers.

    Best-effort: any exception is logged via
    `event=storage_reconciliation_mark_failed` and swallowed. Caller
    has already committed the DB change and propagated the outcome
    to the user; raising here would surface as a 500 on a request
    whose DELETE already succeeded.
    """
    try:
        from app.db.pg_connection import execute as pg_execute

        pg_execute(sql, params)
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_mark_failed "
            "transition=%s reconciliation_id=%s "
            "mark_error=%s mark_error_msg=%r",
            transition, reconciliation_id, type(e).__name__, str(e),
        )


def mark_done(reconciliation_id: str) -> None:
    """Autocommit transition `pending -> done`.

    Used when the post-commit storage delete succeeded or the
    capability was off (no orphan possible). Best-effort: never
    raises.
    """
    _autocommit_update(
        "UPDATE plenora.storage_reconciliation_log "
        "SET state = 'done', updated_at = now() "
        "WHERE id = ?",
        (reconciliation_id,),
        reconciliation_id=reconciliation_id,
        transition="done",
    )


def mark_orphan_deleted(reconciliation_id: str) -> None:
    """Autocommit terminal `pending -> orphan_deleted`.

    Used by the future reconciler beat hook  after a
    successful catch-up delete. Best-effort: never raises.
    """
    _autocommit_update(
        "UPDATE plenora.storage_reconciliation_log "
        "SET state = 'orphan_deleted', updated_at = now() "
        "WHERE id = ?",
        (reconciliation_id,),
        reconciliation_id=reconciliation_id,
        transition="orphan_deleted",
    )


def mark_failed_attempt(
    reconciliation_id: str,
    *,
    error: str,
    next_retry_at: Optional[datetime] = None,
) -> None:
    """Autocommit: increment `attempts`, set `last_error`, optional
    `next_retry_at`.

    Does NOT auto-promote to `lost`: that's the reconciler's
    responsibility , informed by a `MAX_RETRIES` policy
    enforced at the call site (see `mark_lost`). Best-effort:
    never raises.

    Args:
        reconciliation_id: id returned by `record_pending`.
        error: short error description (preferably
            `"<ExcType>: <repr>"`). Persisted in `last_error`.
        next_retry_at: optional UTC `datetime`. If `None`, the
            column stays NULL (reconciler picks it up immediately).
    """
    _autocommit_update(
        "UPDATE plenora.storage_reconciliation_log "
        "SET attempts = attempts + 1, "
        "    last_error = ?, "
        "    next_retry_at = ?, "
        "    updated_at = now() "
        "WHERE id = ?",
        (error, next_retry_at, reconciliation_id),
        reconciliation_id=reconciliation_id,
        transition="failed_attempt",
    )


def mark_lost(reconciliation_id: str, *, error: str) -> None:
    """Autocommit terminal `pending -> lost`.

     used by the reconciler beat hook
    when `attempts` has reached the `STORAGE_RECONCILE_MAX_ATTEMPTS`
    cap and the storage delete still failed. The row stays for
    forensic / dashboard inspection; no further retry is attempted
    until human intervention.

    Best-effort (same as the other `mark_*` helpers): never raises.
    Does NOT increment `attempts` — the call site has just observed
    a failure via `mark_failed_attempt` and decided to promote.

    Args:
        reconciliation_id: id returned by `record_pending`.
        error: short error description persisted in `last_error`
            alongside the terminal state. Same shape as
            `mark_failed_attempt.error`.
    """
    _autocommit_update(
        "UPDATE plenora.storage_reconciliation_log "
        "SET state = 'lost', "
        "    last_error = ?, "
        "    updated_at = now() "
        "WHERE id = ?",
        (error, reconciliation_id),
        reconciliation_id=reconciliation_id,
        transition="lost",
    )


# Reconciler boundary


_FETCH_DUE_PENDING_DELETES_SQL = (
    "SELECT id, domain, storage_key, operation, attempts "
    "FROM plenora.storage_reconciliation_log "
    "WHERE state = 'pending' "
    "  AND operation = 'delete' "
    "  AND (next_retry_at IS NULL OR next_retry_at <= ?) "
    "ORDER BY next_retry_at NULLS FIRST, created_at ASC "
    "LIMIT ?"
)


def fetch_due_pending_deletes(
    *,
    limit: int,
    now: Optional[datetime] = None,
) -> list["PendingReconciliationRow"]:
    """Read-only fetch of pending `operation="delete"` rows due now.

    Uses the partial index `idx_storage_recon_pending` on
    `(state, next_retry_at) WHERE state='pending'`.

    Filters:
      - `state = 'pending'` (terminal states excluded);
      - `operation = 'delete'` (this stream supports only delete —
        upload-intent / W2 race coverage is out-of-scope per task
        brief);
      - `next_retry_at IS NULL OR next_retry_at <= now`. NULL rows
        are picked up immediately (no backoff yet); rows with a
        future retry timestamp are skipped until the cadence ticks
        far enough.

    Order: `next_retry_at NULLS FIRST, created_at ASC`. Ensures
    fresh failures (NULL `next_retry_at` after INSERT) and
    earliest-due retries are processed before later ones. Stable.

    Args:
        limit: maximum rows to return. Caller owns the cap; the
            reconciler passes `STORAGE_RECONCILE_BATCH_SIZE` from
            settings.
        now: UTC `datetime` for the `next_retry_at <= now` filter.
            `None` ⇒ defaults to `datetime.now(timezone.utc)`.

    Returns:
        List of `PendingReconciliationRow`. Empty list if no due
        rows. Best-effort on read error: returns `[]` and logs a
        warning via `event=storage_reconciliation_fetch_failed`.
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"fetch_due_pending_deletes: limit must be a positive int, "
            f"got {limit!r}"
        )

    from datetime import timezone as _tz

    cutoff = now if now is not None else datetime.now(_tz.utc)

    try:
        from app.db.pg_connection import fetchall

        rows = fetchall(_FETCH_DUE_PENDING_DELETES_SQL, (cutoff, limit))
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_fetch_failed "
            "fetch_error=%s fetch_error_msg=%r",
            type(e).__name__, str(e),
        )
        return []

    out: list[PendingReconciliationRow] = []
    for row in rows or ():
        # Tolerant to dict-row (psycopg2 RealDictCursor) and tuple-row
        if isinstance(row, dict):
            row_id = row["id"]
            domain = row["domain"]
            storage_key = row["storage_key"]
            operation = row["operation"]
            attempts = row["attempts"]
        else:
            row_id, domain, storage_key, operation, attempts = row
        out.append(
            PendingReconciliationRow(
                id=str(row_id),
                domain=str(domain),
                storage_key=str(storage_key),
                operation=str(operation),  # type: ignore[arg-type
                attempts=int(attempts) if attempts is not None else 0,
            )
        )
    return out


def compute_next_retry_at(
    attempts: int,
    *,
    base_seconds: int,
    now: Optional[datetime] = None,
) -> datetime:
    """Pure helper: exponential backoff scheduling for the next retry.

    Returns `now + base_seconds * 2 ** attempts`. The `attempts`
    argument is the **post-failure** counter (i.e. the value AFTER
    `mark_failed_attempt` increments it): for the 1st failure pass
    `1`, for the 2nd pass `2`, …

    Caps the multiplier at `2 ** 8 = 256` to avoid astronomical
    delays on a row that's been failing for days. With
    `base_seconds=60`, max is ~4.3h between retries.

    Args:
        attempts: post-failure attempt counter (>= 1).
        base_seconds: base interval. Must be > 0. Caller owns the
            value; the reconciler passes
            `STORAGE_RECONCILE_BACKOFF_BASE_SECONDS` from settings.
        now: UTC `datetime` for the addition. `None` ⇒
            `datetime.now(timezone.utc)`.

    Returns:
        `datetime` UTC, timezone-aware.

    Raises:
        ValueError if `attempts < 1` or `base_seconds <= 0`.
        Pure function — no I/O, no logging.
    """
    if not isinstance(attempts, int) or attempts < 1:
        raise ValueError(
            f"compute_next_retry_at: attempts must be int >= 1, got {attempts!r}"
        )
    if not isinstance(base_seconds, int) or base_seconds <= 0:
        raise ValueError(
            f"compute_next_retry_at: base_seconds must be int > 0, "
            f"got {base_seconds!r}"
        )

    from datetime import timedelta, timezone as _tz

    capped_exponent = min(attempts - 1, 8)
    delta_seconds = base_seconds * (2 ** capped_exponent)
    base_now = now if now is not None else datetime.now(_tz.utc)
    return base_now + timedelta(seconds=delta_seconds)


# Operational guardrails
# Goal: prevent `plenora.storage_reconciliation_log` from becoming a
# silent landfill. Two minimal, additive helpers — NO new transitions
# on existing states, NO new operations, NO new migration
# 1. `count_active_by_state_domain` — read-only aggregation of
# `pending` and `lost` rows by `(state, domain)` for periodic
# summary logs. Terminal `done`/`orphan_deleted` are intentionally
# excluded from "active" because they are the audit trail, not
# operator concern
# 2. `prune_terminal_older_than` — bounded DELETE of `done` /
# `orphan_deleted` rows older than a retention window. Caller
# supplies cap; helper enforces single batched DELETE with
# `LIMIT` semantics emulated via subquery (PG doesn't support
# `DELETE ... LIMIT` directly


@dataclass(frozen=True)
class ReconciliationStateCount:
    """Tipato counter per row di `count_active_by_state_domain`.

    Stable shape per logging strutturato e dashboard future:
      - `state`: `"pending"` | `"lost"` (terminali esclusi
        intenzionalmente da questo helper);
      - `domain`: tag callsite (`"file_source"`, `"result_blob"`,
        ...);
      - `count`: numero di righe nello stato/dominio.
    """

    state: ReconciliationState
    domain: str
    count: int


_COUNT_ACTIVE_BY_STATE_DOMAIN_SQL = (
    "SELECT state, domain, COUNT(*) AS count "
    "FROM plenora.storage_reconciliation_log "
    "WHERE state IN ('pending', 'lost') "
    "GROUP BY state, domain "
    "ORDER BY state ASC, domain ASC"
)


def count_active_by_state_domain() -> list["ReconciliationStateCount"]:
    """Read-only count di righe `pending` e `lost` per `(state, domain)`.

     alimenta il summary log per beat
    run cosi' ops puo' notare orphan accumulation senza ispezionare
    il DB direttamente. Solo `pending` (lavoro non ancora finito) e
    `lost` (richiede review) — i terminali `done`/`orphan_deleted`
    sono audit trail e vengono potati da `prune_terminal_older_than`.

    Best-effort: errori di lettura -> warning strutturato +
    ritorno `[]` (il caller, oggi il beat hook, non puo' permettersi
    di crashare il tick).

    Returns:
        Lista di `ReconciliationStateCount` ordinata per `state` ASC
        poi `domain` ASC. Vuoto se la tabella e' vuota o tutti
        terminali. Su errore di lettura: `[]`.
    """
    try:
        from app.db.pg_connection import fetchall

        rows = fetchall(_COUNT_ACTIVE_BY_STATE_DOMAIN_SQL, ())
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_count_failed "
            "count_error=%s count_error_msg=%r",
            type(e).__name__, str(e),
        )
        return []

    out: list[ReconciliationStateCount] = []
    for row in rows or ():
        if isinstance(row, dict):
            state = row["state"]
            domain = row["domain"]
            count = row["count"]
        else:
            state, domain, count = row
        out.append(
            ReconciliationStateCount(
                state=str(state),  # type: ignore[arg-type
                domain=str(domain),
                count=int(count) if count is not None else 0,
            )
        )
    return out


_PRUNE_TERMINAL_SQL = (
    "DELETE FROM plenora.storage_reconciliation_log "
    "WHERE id IN ("
    "    SELECT id FROM plenora.storage_reconciliation_log "
    "    WHERE state IN ('done', 'orphan_deleted') "
    "      AND updated_at < ? "
    "    ORDER BY updated_at ASC "
    "    LIMIT ?"
    ")"
)


def prune_terminal_older_than(
    *,
    older_than: datetime,
    limit: int,
) -> int:
    """Bounded DELETE di righe terminali `done` / `orphan_deleted`
    con `updated_at < older_than`, fino a `limit` righe.

     la tabella e' append-only nel
    runtime; senza prune i terminali (success path + reconciler
    success path) crescono indefinitamente. Questo helper rimuove
    SOLO i terminali — pending/lost non vengono mai toccati.

    PG non supporta `DELETE ... LIMIT` diretto: il helper usa
    `DELETE WHERE id IN (SELECT ... LIMIT n)` per restare bounded
    per call. La sub-SELECT ordina per `updated_at ASC` cosi' i
    piu' vecchi vengono potati prima.

    Best-effort: errori di esecuzione -> warning strutturato +
    ritorno `0`. Caller (beat hook) non deve poter crashare il
    tick a causa di un prune fallito.

    Args:
        older_than: cutoff `datetime` UTC. Righe con `updated_at`
            strettamente minore sono candidate. tz-aware atteso.
        limit: max righe per call. Deve essere `int > 0`. Caller
            owns il valore (oggi `STORAGE_RECONCILE_PRUNE_BATCH_SIZE`
            da Settings).

    Returns:
        Numero di righe effettivamente cancellate (`cursor.rowcount`).
        `0` su errore o quando non ci sono righe candidate.

    Raises:
        ValueError se `limit <= 0` (invariante caller, non DB).
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"prune_terminal_older_than: limit must be int > 0, got {limit!r}"
        )

    try:
        from app.db.pg_connection import execute as pg_execute

        cur = pg_execute(_PRUNE_TERMINAL_SQL, (older_than, limit))
        return int(getattr(cur, "rowcount", 0) or 0)
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_prune_failed "
            "prune_error=%s prune_error_msg=%r",
            type(e).__name__, str(e),
        )
        return 0


# Upload-intent lifecycle
# Closes R-021.1: `app/services/file_storage_dispatch.py` had an
# upload-then-DB-save W2 orphan race. Pattern is "self-clearing"
# 1. Caller (`dispatch_blob_storage`) records `operation='upload_intent'`
# autocommit BEFORE the upload. No caller-side transaction
# coupling: callers are not in scope for this stream and are not
# modified
# 2. On upload exception, caller invokes `mark_done(intent_id)` —
# no orphan exists because nothing was uploaded
# 3. On upload success, the intent row stays `pending`. The
# reconciler beat hook closes it after a grace window
# if `plenora.file_sources.storage_key` already references the
# key → caller's DB save committed → `mark_done`
# otherwise → `compensating_delete` + `mark_orphan_deleted`
# or backoff + `mark_failed_attempt`, eventually `mark_lost`
# Boundedness: pending upload_intents transition to terminal within
# `grace_seconds + max_attempts * backoff` of creation. Terminal
# rows are pruned by `prune_terminal_older_than` (already wired into
# the beat hook). No state can accumulate forever


_INSERT_UPLOAD_INTENT_SQL = (
    "INSERT INTO plenora.storage_reconciliation_log "
    "(domain, storage_key, operation, state, reason, extras) "
    "VALUES (%s, %s, 'upload_intent', 'pending', %s, %s::jsonb) "
    "RETURNING id"
)


def record_upload_intent(
    *,
    domain: str,
    storage_key: str,
    reason: str,
    extras: Optional[Mapping[str, Any]] = None,
) -> Optional[str]:
    """Autocommit INSERT di una pending `upload_intent` row.

     cattura l'intento di upload
    PRIMA che il blob venga effettivamente caricato sull'object
    storage, cosi' una process death tra upload e caller-side DB
    save (W2 race) lascia comunque una traccia che il reconciler
    puo' chiudere.

    NOT a transactional outbox
    --------------------------
    Questo NON e' un transactional outbox. Differenze sostanziali:

    - L'INSERT autocommit avviene PRIMA della transazione del
      caller (caller's DB save), non dentro la stessa transaction
      del business write. Un crash tra `record_upload_intent` e
      l'upload effettivo lascia una pending row su un blob mai
      caricato; il reconciler la chiude col path "orphan" dopo il
      grace window (compensating_delete e' no-op su key
      inesistente — accettato).
    - La closure dipende da un beat hook periodico
      (`STORAGE_RECONCILE_INTERVAL_SECONDS`) + grace window
      (`STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS`) +
      observation indiretta (`is_storage_key_referenced` su
      `plenora.file_sources.storage_key`). Un vero outbox
      committerebbe la pending entry nella stessa transaction
      della business write e produrrebbe garanzie at-least-once
      sul publisher; qui il garante e' il reconciler heuristico.
    - Reconciler disabilitato (`STORAGE_RECONCILE_INTERVAL_SECONDS<=0`
      OR `STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS<=0` OR
      `STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE<=0`) lascia le
      righe pending in tabella senza closure path. Le ops devono
      sapere che il "self-clearing" pattern dipende dal beat hook
      attivo. Visibilita' via:
        - `count_pending_by_operation` (per-operation counter
          dedicato a questo gap);
        - `gather_readiness.checks.reconciliation_backlog.detail`
          (espone il breakdown per-operation).
    - L'engine alignment (STORAGE_ARTIFACT_ENGINE) e' "compensating
      reconciliation log" — non "outbox/saga framework". L'audit
      `BE_STORAGE_ENGINE_AUDIT.md` §23 documenta i failure modes
      e le closure paths future.

    Differenza chiave da `record_pending`:
    - `record_pending` richiede un cursor del caller e partecipa
      alla transazione del caller (atomic con il DB DELETE);
    - `record_upload_intent` apre la PROPRIA transazione (autocommit)
      perche' il caller (`dispatch_blob_storage`) non possiede
      ancora alcuna transazione DB — la INSERT `file_sources`
      avviene dopo il return.

    Best-effort: errore DB → warning strutturato + ritorno `None`.
    Il caller procede comunque con l'upload (la W2 race per quel
    singolo dispatch resta — comportamento attuale, non peggiora).

    Args:
        domain: tag callsite (`"file_source"`, ...). Non interpretato.
        storage_key: chiave object storage che sara' caricata
            (es. `"file-sources/<id>/<filename>"`).
        reason: motivo persistito (`"upload_intent_pre_dispatch"`).
        extras: optional `Mapping[str, Any]` serialized to JSONB.

    Returns:
        Stringa UUID dell'intent appena inserito, o `None` se la
        INSERT autocommit e' fallita (best-effort).
    """
    extras_json = json.dumps(dict(extras)) if extras else None
    try:
        from app.db.pg_connection import fetchone as pg_fetchone

        row = pg_fetchone(
            _INSERT_UPLOAD_INTENT_SQL,
            (domain, storage_key, reason, extras_json),
        )
        if row is None:
            return None
        new_id = row["id"] if isinstance(row, dict) else row[0]
        return str(new_id)
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_upload_intent_record_failed "
            "domain=%s storage_key=%s "
            "record_error=%s record_error_msg=%r",
            domain, storage_key, type(e).__name__, str(e),
        )
        return None


_FETCH_DUE_STALE_UPLOAD_INTENTS_SQL = (
    "SELECT id, domain, storage_key, operation, attempts "
    "FROM plenora.storage_reconciliation_log "
    "WHERE state = 'pending' "
    "  AND operation = 'upload_intent' "
    "  AND created_at < ? "
    "  AND (next_retry_at IS NULL OR next_retry_at <= ?) "
    "ORDER BY next_retry_at NULLS FIRST, created_at ASC "
    "LIMIT ?"
)


def fetch_due_stale_upload_intents(
    *,
    limit: int,
    grace_seconds: int,
    now: Optional[datetime] = None,
) -> list["PendingReconciliationRow"]:
    """Read-only fetch di `upload_intent` pending stantii.

     il reconciler beat hook chiama
    questo helper DOPO il fetch dei delete-pending. Solo le righe
    `created_at < now - grace_seconds` sono candidate, cosi' un
    upload "in volo" (ancora dentro la finestra normale di
    upload+DB-save) non viene chiuso prematuramente.

    Filtri:
      - `state = 'pending'`;
      - `operation = 'upload_intent'`;
      - `created_at < now - grace_seconds` (grace window);
      - `next_retry_at IS NULL OR <= now` (rispetta backoff).

    Args:
        limit: cap righe per call. Caller (beat hook) passa
            `STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE`.
        grace_seconds: finestra di grazia tra `created_at` e `now`.
            Caller passa `STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS`.
        now: UTC `datetime` per i filtri temporali. `None` ⇒
            `datetime.now(timezone.utc)`.

    Returns:
        Lista di `PendingReconciliationRow` (stessa shape del fetch
        delete). Vuoto su zero righe o errore (best-effort).

    Raises:
        ValueError se `limit <= 0` o `grace_seconds < 0`.
    """
    if not isinstance(limit, int) or limit <= 0:
        raise ValueError(
            f"fetch_due_stale_upload_intents: limit must be a positive int, "
            f"got {limit!r}"
        )
    if not isinstance(grace_seconds, int) or grace_seconds < 0:
        raise ValueError(
            f"fetch_due_stale_upload_intents: grace_seconds must be int >= 0, "
            f"got {grace_seconds!r}"
        )

    from datetime import timedelta as _td, timezone as _tz

    base_now = now if now is not None else datetime.now(_tz.utc)
    grace_cutoff = base_now - _td(seconds=grace_seconds)

    try:
        from app.db.pg_connection import fetchall

        rows = fetchall(
            _FETCH_DUE_STALE_UPLOAD_INTENTS_SQL,
            (grace_cutoff, base_now, limit),
        )
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_upload_intent_fetch_failed "
            "fetch_error=%s fetch_error_msg=%r",
            type(e).__name__, str(e),
        )
        return []

    out: list[PendingReconciliationRow] = []
    for row in rows or ():
        if isinstance(row, dict):
            row_id = row["id"]
            domain = row["domain"]
            storage_key = row["storage_key"]
            operation = row["operation"]
            attempts = row["attempts"]
        else:
            row_id, domain, storage_key, operation, attempts = row
        out.append(
            PendingReconciliationRow(
                id=str(row_id),
                domain=str(domain),
                storage_key=str(storage_key),
                operation=str(operation),  # type: ignore[arg-type
                attempts=int(attempts) if attempts is not None else 0,
            )
        )
    return out


_FILE_SOURCE_STORAGE_KEY_REF_SQL = (
    "SELECT 1 FROM plenora.file_sources "
    "WHERE storage_key = ? LIMIT 1"
)


def is_storage_key_referenced(storage_key: str) -> Optional[bool]:
    """Verifica se `storage_key` e' referenziato da una row in
    `plenora.file_sources` (idx_file_sources_storage_key_notnull).

     il reconciler usa questo helper
    per decidere se un upload_intent stantio rappresenta:
      - `True` → caller's DB save committed; intent va `mark_done`;
      - `False` → orphan candidate; intent va `compensating_delete`
        + `mark_orphan_deleted`;
      - `None` → read-error; il caller DEVE NON procedere alla
        delete (rischio data loss su key live). Il beat hook
        tratta questo caso come `mark_failed_attempt` + retry.

    La distinzione `False` vs `None` e' critica: confondere read-
    error con "non referenziato" porterebbe il reconciler a
    cancellare blob ancora vivi. Il caller MUST branchare
    esplicitamente sui tre stati.

    Args:
        storage_key: chiave object storage da cercare.

    Returns:
        `True` se la riga esiste; `False` se non esiste; `None`
        se la lettura ha fallito (DB unreachable, syntax error,
        ecc.).
    """
    try:
        from app.db.pg_connection import fetchall

        rows = fetchall(_FILE_SOURCE_STORAGE_KEY_REF_SQL, (storage_key,))
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_storage_key_ref_failed "
            "storage_key=%s ref_error=%s ref_error_msg=%r",
            storage_key, type(e).__name__, str(e),
        )
        return None

    return bool(rows)


# Per-operation visibility
# `count_active_by_state_domain` aggregates by `(state, domain)` —
# it does NOT differentiate between `operation='delete'` and
# `operation='upload_intent'` rows. Two failure modes were
# previously invisible
# 1. The upload_intent self-clearing pattern depends on the beat
# hook running. If the reconciler is disabled
# STORAGE_RECONCILE_INTERVAL_SECONDS<=0) or the upload_intent
# branch is disabled (STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS<=0
# or STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE<=0), pending
# upload_intent rows accumulate without closure. The total
# `pending` count from `count_active_by_state_domain` masks
# this: a backlog of 100 pending upload_intents looks like
# 100 pending deletes
# 2. A growing upload_intent backlog signals a different incident
# than a growing delete backlog. Delete backlog ⇒ object
# storage / network problem; upload_intent backlog ⇒
# reconciler disabled OR `is_storage_key_referenced` chronic
# read-error OR mass DB-save rollback storm. Operators need
# to differentiate
# This helper is read-only, swallow-on-error (best-effort
# zero behaviour change to existing callers. The readiness check
# `check_reconciliation_backlog` consumes it to surface the
# breakdown in its detail string and (optionally) trigger a 503
# on a per-operation threshold


_COUNT_PENDING_BY_OPERATION_SQL = (
    "SELECT operation, COUNT(*) AS count "
    "FROM plenora.storage_reconciliation_log "
    "WHERE state = 'pending' "
    "GROUP BY operation "
    "ORDER BY operation ASC"
)


def count_pending_by_operation() -> dict[str, int]:
    """Read-only count di righe `state='pending'` raggruppate per
    `operation` (today: `'delete'` | `'upload_intent'`).

    visibility helper dedicato al gap tra il counter generico
    `count_active_by_state_domain` (per state+domain) e l'esigenza
    operativa di distinguere delete-side W1 backlog da upload_intent-
    side W2 backlog.

    Best-effort: errori di lettura → warning strutturato + ritorno
    `{}` (il caller, oggi `check_reconciliation_backlog`, non puo'
    permettersi di crashare la readiness probe).

    Returns:
        `dict[str, int]` keyed on operation name. Manca chiavi per
        operations che hanno zero pending (es. `{'delete': 12}` se
        nessun upload_intent e' pending). Su errore di lettura: `{}`.
    """
    try:
        from app.db.pg_connection import fetchall

        rows = fetchall(_COUNT_PENDING_BY_OPERATION_SQL, ())
    except Exception as e:
        logger.warning(
            "event=storage_reconciliation_count_by_operation_failed "
            "count_error=%s count_error_msg=%r",
            type(e).__name__, str(e),
        )
        return {}

    out: dict[str, int] = {}
    for row in rows or ():
        if isinstance(row, dict):
            op = row.get("operation")
            count = row.get("count")
        else:
            op, count = row
        if op is None:
            continue
        try:
            out[str(op)] = int(count) if count is not None else 0
        except (TypeError, ValueError):
            continue
    return out
