"""
Intermediate Router — endpoint FastAPI per feature inspection e confronto run.

Endpoints:
  GET  /api/runs/{canvas_id}                          — lista run per canvas
  GET  /api/runs/{run_id}/nodes/{node_id}/data        — righe di un nodo
  GET  /api/runs/compare                              — confronto L1 tra due run
  GET  /api/runs/compare/{run_id_a}/{run_id_b}/diff   — confronto L2 (righe aggiunte/rimosse)
  POST /api/runs/{run_id}/pin                         — pinna/spinna una run
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth.auth import (
    get_current_user,
    require_not_esecutore,
    assert_group_access,
    CurrentUser,
    group_filter,
)
from app.core.intermediate import db as idb
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["intermediate"])


# ============================================================
# MODELS
# ============================================================

class RunSummary(BaseModel):
    run_id: str
    canvas_id: str
    timestamp: str
    status: str
    pinned: bool
    total_rows_in: Optional[int]
    total_rows_out: Optional[int]
    total_rows_dropped: Optional[int]
    total_nodes: Optional[int]
    nodes_ok: Optional[int]
    nodes_error: Optional[int]
    nodes_skipped: Optional[int]
    total_exec_time: Optional[float]
    input_hash: Optional[str]
    output_hash: Optional[str]


class CompareL1Result(BaseModel):
    run_a: RunSummary
    run_b: RunSummary
    same_input: bool
    same_output: bool
    delta_rows_in: Optional[int]
    delta_rows_out: Optional[int]
    delta_exec_time: Optional[float]


class PinRequest(BaseModel):
    pinned: bool = True


# ============================================================
# HELPERS
# ============================================================

def _run_to_summary(run: dict) -> RunSummary:
    return RunSummary(
        run_id=run["run_id"],
        canvas_id=run["canvas_id"],
        timestamp=run["timestamp"],
        status=run["status"],
        pinned=bool(run.get("pinned", 0)),
        total_rows_in=run.get("total_rows_in"),
        total_rows_out=run.get("total_rows_out"),
        total_rows_dropped=run.get("total_rows_dropped"),
        total_nodes=run.get("total_nodes"),
        nodes_ok=run.get("nodes_ok"),
        nodes_error=run.get("nodes_error"),
        nodes_skipped=run.get("nodes_skipped"),
        total_exec_time=run.get("total_exec_time"),
        input_hash=run.get("input_hash"),
        output_hash=run.get("output_hash"),
    )


def _check_run_access(run_id: str, current_user: CurrentUser) -> dict:
    """Verifica che la run esista e appartenga al gruppo. Ritorna run dict."""
    run = idb.get_run(run_id)
    assert_group_access(run, current_user, f"Run {run_id}")
    return run


# ============================================================
# ENDPOINTS
# ============================================================

@router.get("/{canvas_id}", response_model=List[RunSummary])
def list_runs(
    canvas_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista run per canvas, ordinate dalla più recente."""
    try:
        gid = group_filter(current_user)
        # superadmin: gid è None → nessun filtro gruppo
        if gid is None:
            conn = idb.get_connection()
            rows = conn.execute(
                "SELECT * FROM runs WHERE canvas_id = ? ORDER BY timestamp DESC LIMIT ?",
                (canvas_id, limit)
            ).fetchall()
            runs = [dict(r) for r in rows]
        else:
            runs = idb.list_runs(canvas_id, gid, limit=limit)
        return [_run_to_summary(r) for r in runs]
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista runs canvas {canvas_id}: {e}", op="intermediate_api")
        raise HTTPException(status_code=500, detail="Errore recupero runs")


@router.get("/{run_id}/nodes/{node_id}/data")
def get_node_data(
    run_id: str,
    node_id: str,
    limit: int = Query(default=100, ge=1, le=5000),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Righe del nodo da intermediate.db.
    Ritorna max `limit` righe (default 100).
    """
    try:
        _check_run_access(run_id, current_user)
        rows = idb.get_node_table_rows(
            run_id=run_id,
            node_id=node_id,
            group_id=current_user.group_id,
            limit=limit,
        )
        node = idb.get_node_meta(run_id, node_id)
        row_count = node["row_count"] if node and node.get("row_count") is not None else len(rows)
        col_schema = node["col_schema"] if node and node.get("col_schema") is not None else {}
        return {
            "run_id": run_id,
            "node_id": node_id,
            "row_count": row_count,
            "col_schema": col_schema,
            "rows": rows,
            "truncated": row_count > limit,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore dati nodo {node_id} run {run_id}: {e}", op="intermediate_api")
        raise HTTPException(status_code=500, detail="Errore recupero dati nodo")


@router.get("/compare/l1", response_model=CompareL1Result)
def compare_runs_l1(
    run_id_a: str = Query(...),
    run_id_b: str = Query(...),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Confronto L1 tra due run — solo hash aggregati e contatori.
    Veloce, non legge le tabelle dinamiche.
    """
    try:
        run_a = _check_run_access(run_id_a, current_user)
        run_b = _check_run_access(run_id_b, current_user)

        delta_rows_in = None
        delta_rows_out = None
        delta_exec = None

        if run_a.get("total_rows_in") is not None and run_b.get("total_rows_in") is not None:
            delta_rows_in = run_b["total_rows_in"] - run_a["total_rows_in"]
        if run_a.get("total_rows_out") is not None and run_b.get("total_rows_out") is not None:
            delta_rows_out = run_b["total_rows_out"] - run_a["total_rows_out"]
        if run_a.get("total_exec_time") is not None and run_b.get("total_exec_time") is not None:
            delta_exec = run_b["total_exec_time"] - run_a["total_exec_time"]

        same_input = (
            bool(run_a.get("input_hash")) and
            run_a.get("input_hash") == run_b.get("input_hash")
        )
        same_output = (
            bool(run_a.get("output_hash")) and
            run_a.get("output_hash") == run_b.get("output_hash")
        )

        return CompareL1Result(
            run_a=_run_to_summary(run_a),
            run_b=_run_to_summary(run_b),
            same_input=same_input,
            same_output=same_output,
            delta_rows_in=delta_rows_in,
            delta_rows_out=delta_rows_out,
            delta_exec_time=round(delta_exec, 3) if delta_exec is not None else None,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore confronto L1 {run_id_a}/{run_id_b}: {e}", op="intermediate_api")
        raise HTTPException(status_code=500, detail="Errore confronto runs")


@router.get("/compare/l2")
def compare_runs_l2(
    run_id_a: str = Query(...),
    run_id_b: str = Query(...),
    node_id: str = Query(...),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Confronto L2 tra due run sullo stesso nodo.
    Ritorna righe aggiunte in B (non presenti in A) e rimosse da A (non presenti in B),
    confrontando tramite _row_hash.
    """
    try:
        _check_run_access(run_id_a, current_user)
        _check_run_access(run_id_b, current_user)

        # Legge gli hash dalle due tabelle dinamiche
        from app.core.intermediate.db import _safe_table_name
        conn = idb.get_connection()

        table_a = _safe_table_name(run_id_a, node_id)
        table_b = _safe_table_name(run_id_b, node_id)

        # Verifica che entrambe le tabelle esistano
        for tbl in [table_a, table_b]:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
            if not exists:
                raise HTTPException(
                    status_code=404,
                    detail=f"Dati nodo '{node_id}' non trovati per run {run_id_a if tbl == table_a else run_id_b}"
                )

        # Righe rimosse: in A ma non in B
        removed = conn.execute(
            f"SELECT * FROM {table_a} WHERE _row_hash NOT IN (SELECT _row_hash FROM {table_b}) LIMIT 500"
        ).fetchall()

        # Righe aggiunte: in B ma non in A
        added = conn.execute(
            f"SELECT * FROM {table_b} WHERE _row_hash NOT IN (SELECT _row_hash FROM {table_a}) LIMIT 500"
        ).fetchall()

        removed_list = [dict(r) for r in removed]
        added_list = [dict(r) for r in added]

        # Conta totali senza limite
        count_a = conn.execute(f"SELECT COUNT(*) FROM {table_a}").fetchone()[0]
        count_b = conn.execute(f"SELECT COUNT(*) FROM {table_b}").fetchone()[0]

        return {
            "run_id_a": run_id_a,
            "run_id_b": run_id_b,
            "node_id": node_id,
            "count_a": count_a,
            "count_b": count_b,
            "removed_count": len(removed_list),
            "added_count": len(added_list),
            "removed": removed_list,   # righe in A non in B
            "added": added_list,        # righe in B non in A
            "truncated": len(removed_list) == 500 or len(added_list) == 500,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore confronto L2 {run_id_a}/{run_id_b}: {e}", op="intermediate_api")
        raise HTTPException(status_code=500, detail="Errore confronto L2")


@router.post("/{run_id}/pin")
def pin_run(
    run_id: str,
    body: PinRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Pinna o spinna una run — le run pinnate sono escluse dalla retention FIFO."""
    try:
        ok = idb.pin_run(run_id, current_user.group_id, pinned=body.pinned)
        if not ok:
            raise HTTPException(status_code=404, detail=f"Run {run_id} non trovata")
        return {"run_id": run_id, "pinned": body.pinned}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore pin run {run_id}: {e}", op="intermediate_api")
        raise HTTPException(status_code=500, detail="Errore pin run")
