"""
Router API per la consultazione delle run di esecuzione pipeline — PostgreSQL.
"""

import json
from typing import Any, Optional, TypeAlias

from fastapi import APIRouter, Depends, Query, HTTPException
from app.config import settings
# auth dependency callables sourced from the canonical leaf
# `app.core.auth.dependencies`. Identity preserved with the
# compatibility module compatibility export; this router no longer co-depends on the
# login + MFA + JWT compatibility module just to consume the FastAPI Depends
# callables
from app.core.auth.dependencies import (
    require_not_esecutore,
)
from app.shared.contracts.auth import CurrentUser
from app.core.logs.repository import fetchone as _fetchone, fetchall as _fetchall
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http

router = APIRouter(prefix="/pipeline-runs", tags=["runs"])


# Valori canonici della colonna `log_runs.status` (text). Centralizzati
# come costanti perche' il confronto con literal "failure" sparso nel
# codice rende un futuro rename del valore (`"failure"` → `"failed"`)
# silenziosamente non rilevato dal type checker. Tutti i callsite di
# scrittura in workers/tasks/* usano questi stessi literal.
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILURE = "failure"

RunPayload: TypeAlias = dict[str, Any]
LogPayload: TypeAlias = dict[str, Any]


def _run_to_dict(row: RunPayload) -> RunPayload:
    d = dict(row) if row else {}
    if d.get("steps") and isinstance(d["steps"], str):
        try:
            d["steps"] = json.loads(d["steps"])
        except Exception:
            d["steps"] = []
    d.pop("debug_payload", None)
    # Solo None viene rimosso: preservare `steps=[]` (run senza step
    # registrati) e `rows_count=0` come valori espliciti, altrimenti
    # il consumer non puo' distinguere "campo assente" da "vuoto/zero".
    return {k: v for k, v in d.items() if v is not None}


def _log_to_dict(row: LogPayload) -> LogPayload:
    d = dict(row) if row else {}
    if d.get("details") and isinstance(d["details"], str):
        try:
            d["details"] = json.loads(d["details"])
        except Exception:
            pass
    # P1 (2026-05-26): filtra solo `None`, non `""` o `[]`.
    # Pre-fix `v not in (None, "", [])` rimuoveva log con msg vuoto o
    # details list vuota — utili per tracciare eventi "no payload"
    # (es. tick di scheduling senza azione, init senza details).
    # Allineato a `_run_to_dict` poco sopra.
    return {k: v for k, v in d.items() if v is not None}


# GET /api/runs

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
        conditions.append("project_id = %s")
        params.append(project_id)

    if status:
        conditions.append("status = %s")
        params.append(status)

    if module:
        conditions.append("module = %s")
        params.append(module)

    if after:
        conditions.append("ts_start >= %s")
        params.append(after)

    if before:
        conditions.append("ts_start <= %s")
        params.append(before)

    # Isolamento per gruppo: i tenant vedono SOLO il proprio gruppo; gli eventi di
    # sistema (group_id='') sono ristretti ai superadmin (Backend master Part II #9).
    if current_user.role != "superadmin":
        conditions.append("group_id = %s")
        params.append(current_user.group_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    total_row = _fetchone(f"SELECT COUNT(*) as c FROM log_runs {where}", tuple(params))
    # Cast esplicito a int, coerente con il boundary canonico
    # tickets/db.py — vedi commento equivalente in `logs/router.py`.
    total = int(total_row["c"]) if total_row else 0
    rows = _fetchall(
        f"SELECT * FROM log_runs {where} ORDER BY ts_start DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset)
    )

    return {
        "runs":     [_run_to_dict(r) for r in rows],
        "total":    total,
        "limit":    limit,
        "offset":   offset,
        "has_more": offset + limit < total,
    }


# GET /api/pipeline-runs/latest-by-project
# NB: dichiarato PRIMA di `/{run_id}` altrimenti "latest-by-project" verrebbe
# catturato come run_id dal path param.

@router.get("/latest-by-project")
def get_latest_run_by_project(
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> RunPayload:
    """Stato dell'ultima run per ciascun progetto (filtri dashboard #11).

    Una riga per `project_id` via `DISTINCT ON`, ordinata per `ts_start` desc.
    Scoping per gruppo identico a `get_runs` (i non-superadmin vedono solo il
    proprio gruppo). Risposta compatta `{project_id: {status, ts_start}}` —
    pensata per costruire una mappa client-side senza paginare le run.
    """
    conditions = ["project_id IS NOT NULL", "project_id <> ''"]
    params: list[Any] = []
    if current_user.role != "superadmin":
        conditions.append("group_id = %s")
        params.append(current_user.group_id)

    where = f"WHERE {' AND '.join(conditions)}"
    rows = _fetchall(
        f"SELECT DISTINCT ON (project_id) project_id, status, ts_start "
        f"FROM log_runs {where} ORDER BY project_id, ts_start DESC",
        tuple(params),
    )
    return {
        "statuses": {
            r["project_id"]: {"status": r.get("status"), "ts_start": r.get("ts_start")}
            for r in rows
            if r.get("project_id")
        }
    }


# GET /api/runs/{run_id}

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
    row = _fetchone("SELECT * FROM log_runs WHERE run_id = %s", (run_id,))
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
            raise_typed_http(
                code=error_codes.ACCESS_DENIED,
                status_code=403,
                message="Accesso negato",
            )

    if include_logs:
        log_rows = _fetchall(
            "SELECT * FROM logs WHERE run_id = %s ORDER BY id ASC LIMIT %s",
            (run_id, log_limit)
        )
        run["logs"] = [_log_to_dict(r) for r in log_rows]
        run["logs_truncated"] = len(log_rows) == log_limit

    return run


# GET /api/runs/{run_id}/payload

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
        "SELECT run_id, status, group_id, debug_payload FROM log_runs WHERE run_id = %s",
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
            raise_typed_http(
                code=error_codes.ACCESS_DENIED,
                status_code=403,
                message="Accesso negato",
            )

    if d.get("status") != RUN_STATUS_FAILURE:
        raise HTTPException(status_code=400, detail="Il debug_payload è disponibile solo per run fallite")

    raw_payload = d.get("debug_payload")
    if not raw_payload:
        raise HTTPException(status_code=404, detail="debug_payload non disponibile per questa run")

    try:
        payload = json.loads(raw_payload)
    except Exception:
        raise HTTPException(status_code=500, detail="Errore deserializzazione debug_payload")

    return {"run_id": run_id, "payload": payload}
