"""Canonical lifecycle boundary for result cleanup operations."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from app.db.pg_connection import fetchall, transaction
from app.core.resources.storage_reconciliation import (
    mark_done,
    mark_failed_attempt,
    record_pending,
)
from app.shared.storage_compensation import compensating_delete
from app.utils.logger import get_logger


logger = get_logger(__name__)


class CleanupLogger(Protocol):
    def warning(self, msg: str, *args, **kwargs) -> None: ...


def list_project_ids_with_results() -> list[str]:
    """Return distinct project IDs that currently have result rows."""
    rows = fetchall("SELECT DISTINCT project_id FROM results")
    project_ids: list[str] = []
    for row in rows:
        if isinstance(row, dict):
            project_id = row.get("project_id")
        elif row and len(row) > 0:
            project_id = row[0]
        else:
            continue
        if project_id:
            project_ids.append(project_id)
    return project_ids


def delete_result(result_id: str) -> bool:
    """Elimina result. Se S3-backed, pulisce anche l'oggetto S3 (best-effort).

    Flow (W1 race coverage):

      1. `transaction`: SELECT storage_key + DELETE row + (se
         S3-backed) `record_pending` => tutto atomico.
      2. Commit (uscita CM): row DB sparita + entry pending nel
         reconciliation log.
      3. `compensating_delete` (best-effort, fuori transazione).
      4. Outcome:
         - `ok`/`skipped` => `mark_done(reconciliation_ref.id)`;
         - `failed` => `mark_failed_attempt(...)` => il reconciler
           beat hook re-tentera' secondo backoff esponenziale, fino a
           `mark_lost`.

    ADR-0011 contract preservato: storage failure NON propaga; la
    row DB resta cancellata. La differenza vs pre-e' che
    l'orphan e' ora tracked + retry-able.

    Backward-compat: row con `storage_key=None` (BYTEA legacy) salta
    `record_pending` e il post-commit cleanup; comportamento invariato.
    """
    storage_key_to_clean = None
    deleted = False
    reconciliation_ref = None

    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT storage_key FROM results WHERE id = %s FOR UPDATE",
                (result_id,),
            )
            row = cur.fetchone()
            storage_key = row["storage_key"] if row else None

            if storage_key:
                storage_key_to_clean = storage_key
                # record cleanup intent inside the same transaction as
                # the DELETE. Commit advances both atomically.
                reconciliation_ref = record_pending(
                    cur,
                    domain="result_blob",
                    storage_key=storage_key,
                    operation="delete",
                    reason="row_delete_post_commit",
                    extras={"result_id": result_id},
                )

            cur.execute("DELETE FROM results WHERE id = %s", (result_id,))
            deleted = cur.rowcount > 0

    if deleted and storage_key_to_clean:
        outcome = compensating_delete(
            storage_key_to_clean,
            domain="result_blob",
            reason="row_delete_post_commit",
            extras={"result_id": result_id},
            logger=logger,
        )
        if reconciliation_ref is not None:
            if outcome in ("ok", "skipped"):
                mark_done(reconciliation_ref.id)
            else:
                mark_failed_attempt(
                    reconciliation_ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted


def delete_results_for_project(project_id: str) -> int:
    """Elimina tutti i risultati di un progetto.

    Ritorna il numero di righe eliminate.

    Flow: pattern bulk W1.
      1. Pre-fetch `(id, storage_key)` di tutti gli S3-backed.
      2. `transaction`: per ognuno `record_pending` + DELETE row.
      3. Post-commit: per ogni reconciliation_ref, `compensating_delete`
         + `mark_done`/`mark_failed_attempt` per outcome.

    L'INSERT N pending + DELETE all WHERE project_id=%s e' atomico: o si
    committano TUTTE le pending entries con la cancellazione, o nessuna
    (rollback). Cosi' il reconciler beat hook puo' coprire la finestra
    "DB committed -> process death -> S3 orphan" anche sulla bulk path.

    ADR-0011 preservato: storage failure NON propaga. Il caller riceve
    sempre il `rowcount` finale.
    """
    rows = [
        r
        for r in fetchall(
            "SELECT id, storage_key FROM results "
            "WHERE project_id = %s AND storage_key IS NOT NULL",
            (project_id,),
        )
        if r.get("storage_key")
    ]

    deleted_count = 0
    pending_cleanups: list[tuple[str, str, str]] = []

    with transaction() as conn:
        with conn.cursor() as cur:
            if rows:
                for r in rows:
                    ref = record_pending(
                        cur,
                        domain="result_blob",
                        storage_key=r["storage_key"],
                        operation="delete",
                        reason="row_delete_post_commit",
                        extras={
                            "result_id": r["id"],
                            "project_id": project_id,
                        },
                    )
                    pending_cleanups.append(
                        (r["storage_key"], r["id"], ref.id)
                    )

            cur.execute(
                "DELETE FROM results WHERE project_id = %s", (project_id,)
            )
            deleted_count = cur.rowcount

    if pending_cleanups:
        for storage_key, result_id, reconciliation_id in pending_cleanups:
            outcome = compensating_delete(
                storage_key,
                domain="result_blob",
                reason="row_delete_post_commit",
                extras={
                    "result_id": result_id,
                    "project_id": project_id,
                },
                logger=logger,
            )
            if outcome in ("ok", "skipped"):
                mark_done(reconciliation_id)
            else:
                mark_failed_attempt(
                    reconciliation_id,
                    error=f"compensating_delete_outcome={outcome}",
                )

    return deleted_count


def delete_results_for_projects(
    project_ids: Iterable[str],
    log: CleanupLogger,
    *,
    delete_project_func: Callable[[str], int] | None = None,
) -> int:
    """Delete result rows for several projects and return the total count."""
    delete_project = delete_project_func or delete_results_for_project
    results = 0

    for project_id in project_ids:
        try:
            results += delete_project(project_id)
        except Exception as exc:
            log.warning(f"Cascade delete results progetto {project_id}: {exc}")

    return results
