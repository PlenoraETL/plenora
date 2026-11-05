"""Retention and run cleanup boundary for intermediate executions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Callable, Dict, List

from app.core.resources import storage_reconciliation as reconciliation
from app.db.pg_connection import unit_of_work
from app.infrastructure.storage import compensation as storage_compensation
from app.utils.logger import get_logger


logger = get_logger(__name__)

FetchAll = Callable[[str, tuple], List[Dict]]
ListRuns = Callable[[str, str, int], List[Dict]]
DeleteRun = Callable[[str], None]

DEFAULT_RUNS_RETENTION = 5


def _fetchall(sql: str, params: tuple = ()) -> list[Mapping[str, object]]:
    with unit_of_work() as session:
        return session.fetchall(sql, params)


def list_canvas_ids_with_runs(
    *,
    fetchall_func: FetchAll | None = None,
) -> list[str]:
    """Return distinct canvas IDs that currently have intermediate runs."""
    fetchall_boundary = fetchall_func or _fetchall
    rows = fetchall_boundary("SELECT DISTINCT canvas_id FROM runs", ())
    canvas_ids: list[str] = []
    for row in rows:
        canvas_id = row["canvas_id"] if isinstance(row, dict) else row[0]
        if canvas_id:
            canvas_ids.append(canvas_id)
    return canvas_ids


def list_run_counts_by_canvas_group(
    *,
    fetchall_func: FetchAll | None = None,
) -> list[tuple[str, str, int]]:
    """(canvas_id, group_id, numero di run) per ogni canvas con run intermedie."""
    fetchall_boundary = fetchall_func or _fetchall
    rows = fetchall_boundary(
        "SELECT canvas_id, group_id, COUNT(*) AS runs FROM runs GROUP BY canvas_id, group_id",
        (),
    )
    out: list[tuple[str, str, int]] = []
    for row in rows:
        if isinstance(row, dict):
            canvas_id, group_id, runs = row.get("canvas_id"), row.get("group_id"), row.get("runs")
        else:
            canvas_id, group_id, runs = row[0], row[1], row[2]
        if canvas_id:
            out.append((str(canvas_id), str(group_id or ""), int(runs or 0)))
    return out


def apply_runs_retention(
    canvas_id: str,
    group_id: str,
    retention_n: int | None = None,
    *,
    list_runs_func: ListRuns | None = None,
    delete_run_func: DeleteRun | None = None,
    logger_obj=None,
) -> None:
    """Apply FIFO retention to unpinned runs for a canvas."""
    log = logger_obj or logger
    list_boundary = list_runs_func
    delete_boundary = delete_run_func or _delete_run
    try:
        if retention_n is None:
            retention_n = DEFAULT_RUNS_RETENTION

        if list_boundary is None:
            from app.core.intermediate.runs_db import list_runs

            list_boundary = list_runs

        all_runs = list_boundary(canvas_id, group_id, 1000)
        # Solo run terminali sicure sono candidabili. Una run ``running`` o
        # ``completion_uncertain`` e' una barriera di idempotenza: cancellarla
        # renderebbe possibile un replay mentre la write originale e' ancora
        # attiva o ha un esito non verificato.
        unpinned = [
            r for r in all_runs
            if not r.get("pinned")
            and str(r.get("status") or "") in {"completed", "failed"}
        ]

        if len(unpinned) <= retention_n:
            return

        to_delete = unpinned[retention_n:]
        for run in to_delete:
            delete_boundary(run["run_id"])

        log.info(
            f"Retention intermediate: canvas={canvas_id} "
            f"conservate={retention_n} eliminate={len(to_delete)}",
            op="retention",
        )
    except Exception as e:
        log.warning(
            f"Errore retention intermediate ({canvas_id}): {e}",
            op="retention",
        )


def _delete_run(
    run_id: str,
    *,
    fetchall_func: FetchAll | None = None,
    transaction_func=None,
    logger_obj=None,
) -> None:
    """Delete one run and track object-storage artifact cleanup."""
    fetchall_boundary = fetchall_func or _fetchall
    transaction_boundary = transaction_func or unit_of_work
    log = logger_obj or logger

    artifact_rows = fetchall_boundary(
        "SELECT node_id, artifact_uri "
        "FROM node_meta "
        "WHERE run_id = %s "
        "  AND artifact_uri IS NOT NULL "
        "  AND artifact_status IN ('complete', 'failed')",
        (run_id,),
    )

    pending_specs: List[tuple] = []
    for row in artifact_rows:
        node_id = row["node_id"]
        artifact_uri = row["artifact_uri"]
        if not artifact_uri or "://" in artifact_uri:
            continue
        pending_specs.append((node_id, artifact_uri, "data.parquet"))
        if artifact_uri.endswith("/data.parquet"):
            manifest_uri = artifact_uri[: -len("data.parquet")] + "manifest.json"
            pending_specs.append((node_id, manifest_uri, "manifest.json"))

    pending_refs: List[tuple] = []
    with transaction_boundary() as conn:
        with conn.cursor() as cur:
            if pending_specs:
                for node_id, storage_key, kind in pending_specs:
                    ref = reconciliation.record_pending(
                        cur,
                        domain="intermediate_artifact",
                        storage_key=storage_key,
                        operation="delete",
                        reason="run_delete_cascade",
                        extras={
                            "run_id": run_id,
                            "node_id": node_id,
                            "kind": kind,
                        },
                    )
                    pending_refs.append((storage_key, ref))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))

    if pending_refs:
        for storage_key, ref in pending_refs:
            outcome = storage_compensation.compensating_delete(
                storage_key,
                domain="intermediate_artifact",
                reason="run_delete_cascade",
                extras={
                    "run_id": run_id,
                    "reconciliation_id": ref.id,
                },
                logger=log,
            )
            if outcome in ("ok", "skipped"):
                reconciliation.mark_done(ref.id)
            else:
                reconciliation.mark_failed_attempt(
                    ref.id,
                    error=f"compensating_delete_outcome={outcome}",
                )


def delete_all_runs_for_canvas(
    canvas_id: str,
    *,
    fetchall_func: FetchAll | None = None,
    delete_run_func: DeleteRun | None = None,
    logger_obj=None,
) -> int:
    """Delete all runs for a canvas and return the successful delete count."""
    fetchall_boundary = fetchall_func or _fetchall
    delete_boundary = delete_run_func or _delete_run
    log = logger_obj or logger
    try:
        rows = fetchall_boundary(
            "SELECT run_id FROM runs WHERE canvas_id = %s",
            (canvas_id,),
        )
        if not rows:
            return 0
        count = 0
        for row in rows:
            run_id = row["run_id"] if isinstance(row, dict) else row[0]
            try:
                delete_boundary(run_id)
                count += 1
            except Exception as e:
                log.warning(
                    f"delete_all_runs_for_canvas: errore su run {run_id}: {e}",
                    op="delete_project",
                    exc_info=True,
                )
        log.info(
            f"delete_all_runs_for_canvas: canvas={canvas_id} -> {count} run eliminate",
            op="delete_project",
        )
        return count
    except Exception as e:
        log.error(
            f"[CRASH] delete_all_runs_for_canvas: errore canvas={canvas_id}: {e}",
            op="delete_project",
            exc_info=True,
        )
        raise
