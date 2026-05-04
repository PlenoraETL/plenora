"""log_runs DAL — single-purpose typed boundary for `plenora.log_runs`.

BE-GO-READY-CONNECTION-ENGINE-006-ORCHESTRATOR-ROUTER-PG-EXTRACTION.

Today this module exposes one helper, `delete_log_run_by_run_id`,
extracted from `app/core/orchestrator/router.py` so that router
can stop importing `app.db.pg_connection` directly. Future logs
router extraction (BE-GO-READY-CONNECTION-ENGINE-004 follow-up)
will collect the read-side helpers (currently inline `fetchall`
in `app/core/logs/runs_router.py`) here too — designed as a
single-domain DAL exactly for that growth path.

Boundary
========
Pure DAL: thin wrapper over `app.db.pg_connection.execute`. No
business logic, no policy decisions, no imports from `app.core.*`
or `app.services.*`. Logger usage is limited to module-level
diagnostics; the helper itself never logs and never swallows
errors — propagation contract intentional so the caller (which
runs in a best-effort cascade) can decide whether to log+continue
or fail loud.

Schema convention
=================
`plenora.log_runs` is referenced through the pool `search_path`
(see `pg_connection._DB_SCHEMA`), so the unqualified table name
matches the runtime path used by `logs/runs_router.py`. No
schema-qualification leak.
"""

from __future__ import annotations


__all__ = ["delete_log_run_by_run_id"]


def delete_log_run_by_run_id(run_id: str) -> int:
    """Cancella la riga `log_runs` corrispondente a un singolo task.

    Estratto da `orchestrator/router.delete_task` (cleanup per-task,
    NON cascade per-progetto / per-gruppo). Il caller esegue lo step
    in un blocco best-effort (`try/except → failed_steps.append`):
    questa funzione **non** logga e **non** swallowa — propaga
    l'eccezione SQL al caller cosi' che la decisione di
    log+continue resti fuori dalla DAL.

    Args:
        run_id: identificatore del task (`task_id` Celery
            corrisponde a `log_runs.run_id`).

    Returns:
        Numero di righe effettivamente eliminate (`cursor.rowcount`).
        Tipicamente 0 (nessuna riga per quel run_id) o 1 (riga
        eliminata). Valori >1 sono inaspettati ma non bloccanti:
        `log_runs.run_id` non e' UNIQUE-constrained dal DDL, quindi
        il caller deve interpretare il return come "righe-toccate",
        NON come "deleted boolean".

    Raises:
        Qualsiasi errore di `pg_connection.execute` (DB unreachable,
        constraint, ecc.). Il caller (orchestrator router) wrappa
        in `try/except → failed_steps.append("delete_log_runs")`.
    """
    # Lazy import: preserva la monkey-patch convention dei test
    # esistenti che fanno `patch("app.db.pg_connection.execute", ...)`.
    # Il modulo continua a essere leaf: zero side effect a import time
    # del DAL.
    from app.db.pg_connection import execute as pg_execute

    cur = pg_execute("DELETE FROM log_runs WHERE run_id = ?", (run_id,))
    return cur.rowcount
