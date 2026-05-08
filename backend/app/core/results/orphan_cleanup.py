"""Orphan cleanup orchestration for result and intermediate artifacts."""

from __future__ import annotations

from typing import Literal, TypedDict

import app.db.pg_connection as db
from app.core.results.cleanup import delete_results_for_project
from app.utils.logger import get_logger

logger = get_logger(__name__)


class OrphanResultsReport(TypedDict):
    project_ids: list[str]
    rows_deleted: int


class OrphanRunsReport(TypedDict):
    canvas_ids: list[str]
    runs_deleted: int


class OrphanCleanupReport(TypedDict):
    dry_run: bool
    status: Literal["clean", "partial"]
    orphan_results: OrphanResultsReport
    orphan_runs: OrphanRunsReport
    errors: list[str]


def _get_existing_project_ids() -> set[str]:
    """Set di tutti i project_id attualmente in config.projects."""
    rows = db.fetchall("SELECT id FROM projects")
    return {r["id"] for r in rows}


def find_orphan_result_project_ids() -> list[str]:
    """Lista project_id presenti in `results.results` ma non in `config.projects`.

    Ogni elemento e' un project_id orfano (anche se ha piu' righe results
    associate, viene riportato una sola volta).
    """
    existing = _get_existing_project_ids()
    distinct_in_results = db.fetchall(
        "SELECT DISTINCT project_id FROM results"
    )
    orphans: list[str] = []
    for row in distinct_in_results:
        if isinstance(row, dict):
            pid = row.get("project_id")
        elif row and len(row) > 0:
            pid = row[0]
        else:
            continue
        if pid and pid not in existing:
            orphans.append(pid)
    return orphans


def find_orphan_run_canvas_ids() -> list[str]:
    """Lista canvas_id presenti in `intermediate.runs` ma non in `config.projects`.

    `canvas_id` corrisponde a `project_id` per convenzione PLENORA-ETL
    (vedi commento in `intermediate_schema.sql`).
    """
    from app.core.intermediate import db as idb
    existing = _get_existing_project_ids()
    distinct_in_runs = idb.fetchall(
        "SELECT DISTINCT canvas_id FROM runs"
    )
    orphans: list[str] = []
    for row in distinct_in_runs:
        cid = row["canvas_id"] if isinstance(row, dict) else row[0]
        if cid and cid not in existing:
            orphans.append(cid)
    return orphans


def cleanup_orphans(dry_run: bool = False) -> OrphanCleanupReport:
    """Cleanup completo degli orfani in results/intermediate tables.

    Args:
        dry_run: Se True, solo audit (non cancella nulla). Default False.

    Returns:
        dict con il report:
        {
            "dry_run": bool,
            "status": "clean" | "partial", # "partial" se `errors` non vuoto
            "orphan_results": {
                "project_ids": [...],
                "rows_deleted": int # 0 in dry_run
            },
            "orphan_runs": {
                "canvas_ids": [...],
                "runs_deleted": int # 0 in dry_run
            },
            "errors": [...]
        }

    `status` distingue il caso "scan completato, zero orfani o tutti
    cancellati" (clean) da "scan o cancellazione parziale" (partial).
    Senza questa chiave il caller admin non poteva differenziare "tutto
    ok" da "tutto fallito silenziosamente" (report con `rows_deleted=0`
    e `errors=[...]` e' indistinguibile da "niente da fare" a livello
    dict-shape).
    """
    report: OrphanCleanupReport = {
        "dry_run": dry_run,
        "status": "clean",
        "orphan_results": {"project_ids": [], "rows_deleted": 0},
        "orphan_runs": {"canvas_ids": [], "runs_deleted": 0},
        "errors": [],
    }

    # 1. Orphan results in results tables
    try:
        orphan_result_ids = find_orphan_result_project_ids()
        report["orphan_results"]["project_ids"] = orphan_result_ids
        if not dry_run:
            for pid in orphan_result_ids:
                try:
                    n = delete_results_for_project(pid)
                    report["orphan_results"]["rows_deleted"] += n
                except Exception as e:
                    err = f"delete_results_for_project({pid}): {e}"
                    logger.warning(f"orphan_cleanup: {err}")
                    report["errors"].append(err)
    except Exception as e:
        err = f"find_orphan_result_project_ids: {e}"
        logger.error(f"orphan_cleanup: {err}", exc_info=True)
        report["errors"].append(err)

    # 2. Orphan runs in intermediate tables
    try:
        from app.core.intermediate.cleanup import delete_runs_for_canvas
        orphan_canvas_ids = find_orphan_run_canvas_ids()
        report["orphan_runs"]["canvas_ids"] = orphan_canvas_ids
        if not dry_run:
            for cid in orphan_canvas_ids:
                try:
                    n = delete_runs_for_canvas(cid)
                    report["orphan_runs"]["runs_deleted"] += n
                except Exception as e:
                    err = f"delete_runs_for_canvas({cid}): {e}"
                    logger.warning(f"orphan_cleanup: {err}")
                    report["errors"].append(err)
    except Exception as e:
        err = f"find_orphan_run_canvas_ids: {e}"
        logger.error(f"orphan_cleanup: {err}", exc_info=True)
        report["errors"].append(err)

    if report["errors"]:
        report["status"] = "partial"

    if dry_run:
        logger.info(
            f"orphan_cleanup DRY-RUN: "
            f"{len(report['orphan_results']['project_ids'])} orphan results, "
            f"{len(report['orphan_runs']['canvas_ids'])} orphan runs"
        )
    else:
        _log = logger.warning if report["status"] == "partial" else logger.info
        _log(
            f"orphan_cleanup: status={report['status']} "
            f"results={report['orphan_results']['rows_deleted']} righe, "
            f"runs={report['orphan_runs']['runs_deleted']} run, "
            f"errors={len(report['errors'])}"
        )
    return report
