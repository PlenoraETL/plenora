"""
Router API per la consultazione delle run di esecuzione pipeline.

Endpoints:
- GET  /api/runs                  → lista run paginate con filtri
- GET  /api/runs/{run_id}         → dettaglio run + log tecnici correlati
- GET  /api/runs/{run_id}/payload → debug_payload per il Debug Agent
"""

import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from app.config import settings
from app.core.auth.auth import get_current_user, CurrentUser

router = APIRouter(prefix="/pipeline-runs", tags=["runs"])

LOG_DB_PATH = settings.LOGS_DIR / settings.LOG_DB_FILE


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LOG_DB_PATH), timeout=settings.LOG_DB_CONNECT_TIMEOUT)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _run_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Deserializza steps JSON
    if d.get("steps"):
        try:
            d["steps"] = json.loads(d["steps"])
        except Exception:
            d["steps"] = []
    # debug_payload non viene restituito nella lista — solo in /runs/{id}/payload
    d.pop("debug_payload", None)
    return {k: v for k, v in d.items() if v not in (None, "", [])}


def _log_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    if d.get("details"):
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
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Lista run paginate, ordinate dalla più recente.
    Superadmin vede tutto, gli altri solo il proprio gruppo.
    """
    if not LOG_DB_PATH.exists():
        return {"runs": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}

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

    with _get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM runs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM runs {where} ORDER BY ts_start DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

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
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Dettaglio run: metadati + steps + (opzionale) log tecnici correlati.
    debug_payload escluso — usa /runs/{run_id}/payload per il Debug Agent.
    """
    if not LOG_DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Run non trovata")

    with _get_conn() as conn:
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Run non trovata")

    run = _run_to_dict(row)

    # Controllo accesso gruppo
    if current_user.role != "superadmin":
        run_group = dict(row).get("group_id", "")
        if run_group and run_group != current_user.group_id:
            raise HTTPException(status_code=403, detail="Accesso negato")

    if include_logs:
        with _get_conn() as conn:
            log_rows = conn.execute(
                "SELECT * FROM logs WHERE run_id = ? ORDER BY id ASC LIMIT ?",
                (run_id, log_limit)
            ).fetchall()
        run["logs"] = [_log_to_dict(r) for r in log_rows]
        run["logs_truncated"] = len(log_rows) == log_limit

    return run


# =============================================================================
# GET /api/runs/{run_id}/payload
# =============================================================================

@router.get("/{run_id}/payload")
def get_run_payload(
    run_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """
    Restituisce il debug_payload grezzo per il Debug Agent.
    Solo run in status='failure' hanno un payload.
    """
    if not LOG_DB_PATH.exists():
        raise HTTPException(status_code=404, detail="Run non trovata")

    with _get_conn() as conn:
        row = conn.execute(
            "SELECT run_id, status, group_id, debug_payload FROM runs WHERE run_id = ?",
            (run_id,)
        ).fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Run non trovata")

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
