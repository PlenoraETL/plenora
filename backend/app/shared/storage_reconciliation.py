"""Persistent reconciliation log for storage compensating-delete intent.

BE-GO-READY-STORAGE-ENGINE-005-RECONCILIATION-LOG-FOUNDATION.

Goal
----
Track orphan-cleanup intent across the W1 race window — the gap
between a row's DB DELETE (committed) and the post-commit storage
delete. Today, if the worker dies in that window the blob becomes
an orphan with no application-side trail. ADR-0011 accepts this
("orphan accepted on storage failure"); this module preserves that
posture but persists the intent so a future reconciler beat hook
(batch 006) can act on it.

Boundary
--------
Pure leaf module:
- stdlib + `psycopg2` cursor types passed by caller;
- `app.db.pg_connection.execute` for autocommit transitions.
- Zero FastAPI, zero `app.config.settings`, zero `boto3`.

Three responsibilities:
1. ``record_pending(cursor, ...)`` — INSERT inside the caller's
   transaction. Atomic with the caller's DB change: if the caller
   commits, the pending row exists; if the caller rolls back, no
   reconciliation row leaks.
2. ``mark_done(reconciliation_id)`` — autocommit terminal for the
   happy path (storage delete succeeded or capability disabled).
3. ``mark_failed_attempt(reconciliation_id, *, error, ...)`` —
   autocommit increment of attempts + last_error. Does NOT promote
   to `lost`; that is the reconciler's job (batch 006).

Plus ``mark_orphan_deleted`` for the future reconciler terminal.

Failure semantics
-----------------
- ``record_pending`` propagates SQL errors to the caller. The
  caller owns the transaction and a SQL error there should
  rollback the caller's work too.
- ``mark_done`` / ``mark_failed_attempt`` / ``mark_orphan_deleted``
  are best-effort: they run AFTER the caller has already committed
  the DB change and propagated outcome to the user. An exception
  there would surface as a 500 to a request whose DELETE already
  succeeded. They swallow exceptions and emit a structured
  ``event=storage_reconciliation_mark_failed`` warning.

Go-ready mapping
----------------
::

    type ReconciliationOperation string  // "delete" | "upload_intent"
    type ReconciliationState     string  // "pending" | "done" | "orphan_deleted" | "lost"

    type ReconciliationEntryRef struct {
        ID         string
        Domain     string
        StorageKey string
        Operation  ReconciliationOperation
    }

    func RecordPending(cur Cursor, p RecordPendingParams) (ReconciliationEntryRef, error)
    func MarkDone(reconciliationID string)
    func MarkFailedAttempt(reconciliationID string, p MarkFailedParams)
    func MarkOrphanDeleted(reconciliationID string)
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
    "record_pending",
    "mark_done",
    "mark_failed_attempt",
    "mark_orphan_deleted",
    "mark_lost",
    "fetch_due_pending_deletes",
    "compute_next_retry_at",
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

    BE-GO-READY-STORAGE-ENGINE-006: shape minima necessaria al beat
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

    The caller MUST be inside a `transaction()` context manager (or
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
            batch 008 (W2 race coverage, not implemented here).
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

    Used by the future reconciler beat hook (batch 006) after a
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
    responsibility (batch 006), informed by a `MAX_RETRIES` policy
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

    BE-GO-READY-STORAGE-ENGINE-006: used by the reconciler beat hook
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


# ---------------------------------------------------------------------------
# Reconciler boundary (BE-GO-READY-STORAGE-ENGINE-006)
# ---------------------------------------------------------------------------


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
    fresh failures (NULL `next_retry_at` after batch 005 INSERT) and
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
        # Tolerant to dict-row (psycopg2 RealDictCursor) and tuple-row.
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
                operation=str(operation),  # type: ignore[arg-type]
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
