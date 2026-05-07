"""
log_runs DAL — single-purpose typed boundary for `plenora.log_runs`.

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
    # esistenti che fanno `patch("app.db.pg_connection.execute", ...)`
    # Il modulo continua a essere leaf: zero side effect a import time
    # del DAL
    from app.db.pg_connection import execute as pg_execute

    cur = pg_execute("DELETE FROM log_runs WHERE run_id = ?", (run_id,))
    return cur.rowcount
