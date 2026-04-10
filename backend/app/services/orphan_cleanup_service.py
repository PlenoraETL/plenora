"""
Servizio di cleanup degli orfani cross-DB di URBY-ETL.

Contesto
========
URBY-ETL ha 3 SQLite separati:
  - config.db       (progetti, utenti, ecc.)
  - results.db      (output finali pipeline)
  - intermediate.db (run intermedie + tabelle dinamiche)

`results.results.project_id` e `intermediate.runs.canvas_id` referenziano
logicamente `config.projects.id` ma non è una FK enforced (SQLite non
supporta FK cross-database). Questo significa che se un progetto viene
cancellato senza usare `ProjectService.delete_project` (es. cancellazione
diretta dal DB, crash a metà del cleanup, restore parziale, ecc.), restano
orfani in results.db e intermediate.db.

Questo servizio:
  - Trova orfani (project_id che non esistono più in config.projects)
  - Cancella orfani (sia results blob che intermediate runs+tabelle dinamiche)
  - Supporta dry-run per audit prima del fix

API
===
  find_orphan_result_project_ids() -> List[str]
  find_orphan_run_canvas_ids() -> List[str]
  cleanup_orphans(dry_run: bool = False) -> dict

Quando usarlo
=============
  - Endpoint admin POST /projects/admin/cleanup-orphans (manualmente)
  - Job periodico (es. cron settimanale) — non implementato qui ma è facile
    da wrappare con celery beat o un comando CLI
"""

from typing import List, Dict, Any

import app.db.db_service as db
import app.db.results_db_service as results_db
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _get_existing_project_ids() -> set:
    """Set di tutti i project_id attualmente in config.projects."""
    rows = db.fetchall("SELECT id FROM projects")
    return {r["id"] for r in rows}


def find_orphan_result_project_ids() -> List[str]:
    """Lista project_id presenti in `results.results` ma non in `config.projects`.

    Ogni elemento è un project_id orfano (anche se ha più righe results
    associate, viene riportato una sola volta).
    """
    existing = _get_existing_project_ids()
    distinct_in_results = results_db.fetchall(
        "SELECT DISTINCT project_id FROM results"
    )
    orphans = []
    for row in distinct_in_results:
        pid = row["project_id"] if isinstance(row, dict) else row[0]
        if pid and pid not in existing:
            orphans.append(pid)
    return orphans


def find_orphan_run_canvas_ids() -> List[str]:
    """Lista canvas_id presenti in `intermediate.runs` ma non in `config.projects`.

    `canvas_id` corrisponde a `project_id` per convenzione URBY-ETL
    (vedi commento in `intermediate_schema.sql`).
    """
    from app.core.intermediate import db as idb
    existing = _get_existing_project_ids()
    distinct_in_runs = idb.fetchall(
        "SELECT DISTINCT canvas_id FROM runs"
    )
    orphans = []
    for row in distinct_in_runs:
        cid = row["canvas_id"] if isinstance(row, dict) else row[0]
        if cid and cid not in existing:
            orphans.append(cid)
    return orphans


def cleanup_orphans(dry_run: bool = False) -> Dict[str, Any]:
    """Cleanup completo degli orfani in results.db e intermediate.db.

    Args:
        dry_run: Se True, solo audit (non cancella nulla). Default False.

    Returns:
        dict con il report:
        {
            "dry_run": bool,
            "orphan_results": {
                "project_ids": [...],
                "rows_deleted": int      # 0 in dry_run
            },
            "orphan_runs": {
                "canvas_ids": [...],
                "runs_deleted": int      # 0 in dry_run
            },
            "errors": [...]
        }
    """
    report = {
        "dry_run": dry_run,
        "orphan_results": {"project_ids": [], "rows_deleted": 0},
        "orphan_runs": {"canvas_ids": [], "runs_deleted": 0},
        "errors": [],
    }

    # 1. Orphan results in results.db
    try:
        orphan_result_ids = find_orphan_result_project_ids()
        report["orphan_results"]["project_ids"] = orphan_result_ids
        if not dry_run:
            for pid in orphan_result_ids:
                try:
                    n = results_db.delete_results_for_project(pid)
                    report["orphan_results"]["rows_deleted"] += n
                except Exception as e:
                    err = f"delete_results_for_project({pid}): {e}"
                    logger.warning(f"orphan_cleanup: {err}")
                    report["errors"].append(err)
    except Exception as e:
        err = f"find_orphan_result_project_ids: {e}"
        logger.error(f"orphan_cleanup: {err}", exc_info=True)
        report["errors"].append(err)

    # 2. Orphan runs in intermediate.db
    try:
        from app.core.intermediate.db import delete_all_runs_for_canvas
        orphan_canvas_ids = find_orphan_run_canvas_ids()
        report["orphan_runs"]["canvas_ids"] = orphan_canvas_ids
        if not dry_run:
            for cid in orphan_canvas_ids:
                try:
                    n = delete_all_runs_for_canvas(cid)
                    report["orphan_runs"]["runs_deleted"] += n
                except Exception as e:
                    err = f"delete_all_runs_for_canvas({cid}): {e}"
                    logger.warning(f"orphan_cleanup: {err}")
                    report["errors"].append(err)
    except Exception as e:
        err = f"find_orphan_run_canvas_ids: {e}"
        logger.error(f"orphan_cleanup: {err}", exc_info=True)
        report["errors"].append(err)

    if dry_run:
        logger.info(
            f"orphan_cleanup DRY-RUN: "
            f"{len(report['orphan_results']['project_ids'])} orphan results, "
            f"{len(report['orphan_runs']['canvas_ids'])} orphan runs"
        )
    else:
        logger.info(
            f"orphan_cleanup: "
            f"results={report['orphan_results']['rows_deleted']} righe, "
            f"runs={report['orphan_runs']['runs_deleted']} run, "
            f"errors={len(report['errors'])}"
        )
    return report
