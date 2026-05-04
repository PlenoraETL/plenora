"""
Router API per la consultazione delle run di esecuzione pipeline — PostgreSQL.
"""

import json
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from app.config import settings
from app.core.auth.auth import get_current_user, require_not_esecutore, CurrentUser
from app.db.pg_connection import fetchone as _fetchone, fetchall as _fetchall
from app.shared import error_codes

router = APIRouter(prefix="/pipeline-runs", tags=["runs"])


def _run_to_dict(row: dict) -> dict:
    d = dict(row) if row else {}
    if d.get("steps") and isinstance(d["steps"], str):
        try:
            d["steps"] = json.loads(d["steps"])
        except Exception:
            d["steps"] = []
    d.pop("debug_payload", None)
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _log_to_dict(row: dict) -> dict:
    d = dict(row) if row else {}
    if d.get("details") and isinstance(d["details"], str):
        try:
            d["details"] = json.loads(d["details"])
        except Exception:
            pass
    return {k: v for k, v in d.items() if v not in (None, "", [])}


# =============================================================================
# GET /api/runs
# =============================================================================

@router.get("")
def get_runs(
    project_id: Optional[str] = Query(None),
    status:     Optional[str] = Query(None, description="running / success / failure"),
    module:     Optional[str] = Query(None, description="estrai / manipola / arricchisci"),
    after:      Optional[str] = Query(None, description="ts_start >= (ISO format)"),
    before:     Optional[str] = Query(None, description="ts_start <= (ISO format)"),
    limit:  int = Query(settings.RUNS_PAGE_SIZE, ge=1, le=500),
    offset: int = Query(0, ge=0),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Lista run paginate, ordinate dalla più recente.
    Superadmin vede tutto, gli altri solo il proprio gruppo.
    """
    conditions = []
    params = []

    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if module:
        conditions.append("module = ?")
        params.append(module)

    if after:
        conditions.append("ts_start >= ?")
        params.append(after)

    if before:
        conditions.append("ts_start <= ?")
        params.append(before)

    # Isolamento per gruppo
    if current_user.role != "superadmin":
        conditions.append("(group_id = ? OR group_id = '')")
        params.append(current_user.group_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = _fetchone(f"SELECT COUNT(*) as c FROM log_runs {where}", tuple(params))
    total = total_row["c"] if total_row else 0
    rows = _fetchall(
        f"SELECT * FROM log_runs {where} ORDER BY ts_start DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset)
    )

    return {
        "runs":     [_run_to_dict(r) for r in rows],
        "total":    total,
        "limit":    limit,
        "offset":   offset,
        "has_more": offset + limit < total,
    }


# =============================================================================
# GET /api/runs/{run_id}
# =============================================================================

@router.get("/{run_id}")
def get_run(
    run_id: str,
    include_logs: bool = Query(True, description="Includi log tecnici correlati"),
    log_limit: int = Query(settings.RUNS_LOG_LIMIT, ge=1, le=1000),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Dettaglio run: metadati + steps + (opzionale) log tecnici correlati.
    debug_payload escluso — usa /runs/{run_id}/payload per il Debug Agent.
    """
    row = _fetchone("SELECT * FROM log_runs WHERE run_id = ?", (run_id,))
    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.RUN_NOT_FOUND,
                "message": "Run non trovata",
            },
        )

    run = _run_to_dict(row)

    if current_user.role != "superadmin":
        run_group = row.get("group_id", "")
        if run_group and run_group != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")

    if include_logs:
        log_rows = _fetchall(
            "SELECT * FROM logs WHERE run_id = ? ORDER BY id ASC LIMIT ?",
            (run_id, log_limit)
        )
        run["logs"] = [_log_to_dict(r) for r in log_rows]
        run["logs_truncated"] = len(log_rows) == log_limit

    return run


# =============================================================================
# GET /api/runs/{run_id}/payload
# =============================================================================

@router.get("/{run_id}/payload")
def get_run_payload(
    run_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Restituisce il debug_payload grezzo per il Debug Agent.
    Solo run in status='failure' hanno un payload.
    """
    row = _fetchone(
        "SELECT run_id, status, group_id, debug_payload FROM log_runs WHERE run_id = ?",
        (run_id,)
    )

    if not row:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.RUN_NOT_FOUND,
                "message": "Run non trovata",
            },
        )

    d = dict(row)

    # Controllo accesso gruppo
    if current_user.role != "superadmin":
        run_group = d.get("group_id", "")
        if run_group and run_group != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")

    if d.get("status") != "failure":
        raise HTTPException(status_code=400, detail="Il debug_payload è disponibile solo per run fallite")

    raw_payload = d.get("debug_payload")
    if not raw_payload:
        raise HTTPException(status_code=404, detail="debug_payload non disponibile per questa run")

    try:
        payload = json.loads(raw_payload)
    except Exception:
        raise HTTPException(status_code=500, detail="Errore deserializzazione debug_payload")

    return {"run_id": run_id, "payload": payload}
