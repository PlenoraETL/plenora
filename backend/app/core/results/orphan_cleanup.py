"""Orphan cleanup orchestration for result and intermediate artifacts."""

from __future__ import annotations

from typing import Literal, TypedDict

from app.core.intermediate.cleanup import (
    delete_runs_for_canvas,
    list_canvas_ids_with_runs,
)
from app.core.projects.cleanup import list_existing_project_ids
from app.core.results.cleanup import (
    delete_results_for_project,
    list_project_ids_with_results,
)
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
    return list_existing_project_ids()


def find_orphan_result_project_ids() -> list[str]:
    """Lista project_id presenti in `results.results` ma non in `config.projects`.

    Ogni elemento e' un project_id orfano (anche se ha piu' righe results
    associate, viene riportato una sola volta).
    """
    existing = _get_existing_project_ids()
    return [
        project_id
        for project_id in list_project_ids_with_results()
        if project_id not in existing
    ]


def find_orphan_run_canvas_ids() -> list[str]:
    """Lista canvas_id presenti in `intermediate.runs` ma non in `config.projects`.

    `canvas_id` corrisponde a `project_id` per convenzione PLENORA-ETL
    (vedi commento in `intermediate_schema.sql`).
    """
    existing = _get_existing_project_ids()
    return [
        canvas_id
        for canvas_id in list_canvas_ids_with_runs()
        if canvas_id not in existing
    ]


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
