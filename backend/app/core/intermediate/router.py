"""
Intermediate Router — endpoint FastAPI per feature inspection e confronto run.

Endpoints:
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.core.auth.dependencies import (
    get_current_user,
    require_not_esecutore,
)
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import assert_group_access
from app.core.intermediate import db as idb
from app.shared import error_codes
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/runs", tags=["intermediate"])


# FIXME: #23 (2026-04-28): JSON spec non ammette NaN / +Inf / -Inf
# `db.get_node_table_rows` ricostruisce questi valori dai sentinel
# JSONB persistiti (`_from_jsonable_value`) per garantire hash
# roundtrip-safe. Sul boundary HTTP non serve quella semantica
# FastAPI/json.dumps(allow_nan=False) solleva ValueError. Mappiamo
# qui i non-finite float a None *solo nella response*. Niente
# mutazione di node_rows, niente impatto sull'hash
import math


def _sanitize_for_json(value):
    """Mappa NaN/+Inf/-Inf a None ricorsivamente.

    Lascia invariato il resto (int, str, bool, None, datetime-as-str,
    sub-list/dict). Equivalente a `df.replace({np.nan: None, np.inf:
    None, -np.inf: None})` ma operante su dict/list gia' costruiti.
    """
    if isinstance(value, float):
        # NaN: nan != nan; +/-Inf: math.isfinite False ma not nan
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, list):
        return [_sanitize_for_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _sanitize_for_json(v) for k, v in value.items()}
    return value


# MODELS

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


# HELPERS

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


# ENDPOINTS

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
            from app.db.pg_connection import fetchall as _pg_fetchall
            runs = _pg_fetchall(
                "SELECT * FROM runs WHERE canvas_id = ? ORDER BY timestamp DESC LIMIT ?",
                (canvas_id, limit)
            )
        else:
            runs = idb.list_runs(canvas_id, gid, limit=limit)
        return [_run_to_summary(r) for r in runs]
    except Exception as e:
        logger.error(f"[GESTITO] Errore lista runs canvas {canvas_id}: {e}", op="intermediate_api", exc_info=True)
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
        run = _check_run_access(run_id, current_user)
        # superadmin puo' leggere run di qualsiasi gruppo (access check
        # passa). Per evitare che `get_node_table_rows` applichi comunque
        # `group_id=current_user.group_id` come filtro (rendendo il
        # fetch vuoto per run di altri gruppi
        # superadmin → None (nessun filtro, vede tutto
        # altri → group della RUN (post-check), non dell'utente
        if current_user.role == "superadmin":
            effective_group = None
        else:
            effective_group = (run or {}).get("group_id") or current_user.group_id
        node = idb.get_node_meta(run_id, node_id)
        # preview da artifact (2026-04-28): dispatch
        # per `persistence_mode`
        # full_artifact + status=complete -> read parquet (object storage
        # full_artifact + altri status -> 200 con rows=[] + warning
        # metadata_only -> 200 con rows=[] + warning
        # full_pg / preview_only / NULL -> read da node_rows (legacy
        # Tutti i path sanitizzano NaN/Inf via `_sanitize_for_json`
        # Bug #23) prima del JSONResponse
        from app.core.intermediate.policy import (
            persistence_mode_for_record,
            MODE_FULL_ARTIFACT,
            MODE_METADATA_ONLY,
            ARTIFACT_STATUS_COMPLETE,
        )

        node_dict = node or {}
        row_count = (
            node_dict.get("row_count")
            if node_dict.get("row_count") is not None
            else 0
        )
        col_schema = (
            node_dict.get("col_schema")
            if node_dict.get("col_schema") is not None
            else {}
        )
        persistence_mode = persistence_mode_for_record(node_dict)
        artifact_uri = node_dict.get("artifact_uri")
        artifact_status = node_dict.get("artifact_status")
        artifact_format = node_dict.get("artifact_format")
        persisted_rows_count = node_dict.get("persisted_rows_count")
        preview_truncated_meta = bool(node_dict.get("preview_truncated"))

        rows: list = []
        message: Optional[str] = None

        if persistence_mode == MODE_FULL_ARTIFACT:
            if artifact_status == ARTIFACT_STATUS_COMPLETE and artifact_uri:
                from app.core.intermediate.artifact_reader import (
                    load_preview_from_artifact,
                )
                preview = load_preview_from_artifact(
                    artifact_uri, limit=limit,
                )
                if preview.error:
                    # 200 operativo: niente 500 sul boundary HTTP
                    rows = []
                    message = (
                        f"Preview da artifact non disponibile: {preview.error}"
                    )
                else:
                    rows = preview.rows
                    # Aggiorna col_schema dal Parquet (autorevole sui
                    # tipi reali post-roundtrip) se il meta era vuoto
                    if not col_schema:
                        col_schema = preview.col_schema
                    # row_count autorevole resta da node_meta; il
                    # parquet conferma se il meta era stale
                    if not row_count:
                        row_count = preview.row_count
            else:
                rows = []
                # Stati possibili: pending, failed, none, missing
                msg_status = artifact_status or "none"
                if msg_status == "failed":
                    artifact_error = node_dict.get("artifact_error") or "errore non specificato"
                    message = (
                        f"Artifact preview non disponibile "
                        f"(status=failed): {artifact_error}"
                    )
                elif msg_status == "pending":
                    message = (
                        "Artifact preview non ancora pronto "
                        "(status=pending). Riprova fra qualche secondo."
                    )
                else:
                    message = (
                        f"Artifact preview non disponibile "
                        f"(status={msg_status})."
                    )
        elif persistence_mode == MODE_METADATA_ONLY:
            rows = []
            message = (
                "Dati intermedi non salvati per questo nodo "
                "(persistence_mode=metadata_only). "
                "node_meta + node_hash sono comunque disponibili."
            )
        else:
            # full_pg / preview_only / legacy NULL -> legge node_rows
            rows = idb.get_node_table_rows(
                run_id=run_id,
                node_id=node_id,
                group_id=effective_group,
                limit=limit,
            )

        # FIXME: #23: NaN / +Inf / -Inf non sono JSON compliant
        # Vale per tutte le sorgenti (node_rows, artifact, fallback
        rows = _sanitize_for_json(rows)
        col_schema = _sanitize_for_json(col_schema)

        response = {
            "run_id": run_id,
            "node_id": node_id,
            "row_count": row_count,
            "col_schema": col_schema,
            "rows": rows,
            "truncated": (row_count or 0) > limit,
            "persistence_mode": persistence_mode,
            "persisted_rows_count": persisted_rows_count,
            "preview_truncated": preview_truncated_meta,
            "artifact_uri": artifact_uri,
            "artifact_status": artifact_status,
            "artifact_format": artifact_format,
        }
        if message:
            response["message"] = message
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore dati nodo {node_id} run {run_id}: {e}", op="intermediate_api", exc_info=True)
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
        logger.error(f"[GESTITO] Errore confronto L1 {run_id_a}/{run_id_b}: {e}", op="intermediate_api", exc_info=True)
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
    Ritorna righe aggiunte in B (non presenti in A) e rimosse da A (non
    presenti in B), confrontando tramite row_hash.

    semantics (2026-04-28): dispatch su
    `persistence_mode` di entrambe le run. L'endpoint NON finge un
    diff completo se i dati non sono entrambi su `node_rows`:
      - full_pg + full_pg (o legacy NULL): 200, diff completo come prima.
      - preview_only + preview_only: 200, diff parziale dichiarato
        (`partial=true`, `reason="preview_only"`).
      - metadata_only su almeno un lato: 422 "diff_available=false,
        reason=metadata_only_no_rows".
      - full_artifact + complete: 501 "non ancora implementato"
        (diff completo da artifact = ).
      - full_artifact pending/failed/missing: 422 "non pronto".
      - mode mismatch (preview_only vs full_pg): 422 conservativo.

    Dopo il refactor a tabella unificata plenora.node_rows, il diff
    full e' una singola query con NOT EXISTS che sfrutta l'indice
    (run_id, node_id, row_hash).
    """
    try:
        _check_run_access(run_id_a, current_user)
        _check_run_access(run_id_b, current_user)

        from app.db.pg_connection import fetchall, fetchone
        from app.core.intermediate.policy import (
            persistence_mode_for_record,
            MODE_FULL_PG,
            MODE_PREVIEW_ONLY,
            MODE_METADATA_ONLY,
            MODE_FULL_ARTIFACT,
            ARTIFACT_STATUS_COMPLETE,
        )

        # Verifica che il nodo esista in entrambe le run via node_meta
        # piu' robusto che controllare la presenza di righe in node_rows
        # un nodo potrebbe essere stato eseguito con 0 righe di output
        meta_a = idb.get_node_meta(run_id_a, node_id)
        meta_b = idb.get_node_meta(run_id_b, node_id)
        if not meta_a:
            raise HTTPException(
                status_code=404,
                detail=f"Dati nodo '{node_id}' non trovati per run {run_id_a}"
            )
        if not meta_b:
            raise HTTPException(
                status_code=404,
                detail=f"Dati nodo '{node_id}' non trovati per run {run_id_b}"
            )

        # Classifica le mode (legacy NULL -> "full_pg" via helper
        mode_a = persistence_mode_for_record(meta_a)
        mode_b = persistence_mode_for_record(meta_b)

        def _common(reason: str, message: str, *, diff_available: bool) -> dict:
            return {
                "run_id_a": run_id_a,
                "run_id_b": run_id_b,
                "node_id": node_id,
                "persistence_mode_a": mode_a,
                "persistence_mode_b": mode_b,
                "diff_available": diff_available,
                "reason": reason,
                "message": message,
            }


        # full_artifact e' supportato in scrittura (parquet+manifest
        # ma il diff completo da artifact richiede download + set-diff
        # cross-run. Out of scope di questo batch: rispondiamo 501
        # esplicito invece di silently fallback su node_rows vuoti
        if MODE_FULL_ARTIFACT in (mode_a, mode_b):
            # Se uno dei due e' artifact, distinguiamo "complete vs altro"
            def _artifact_status(meta):
                return (meta or {}).get("artifact_status")
            status_a = _artifact_status(meta_a) if mode_a == MODE_FULL_ARTIFACT else None
            status_b = _artifact_status(meta_b) if mode_b == MODE_FULL_ARTIFACT else None
            both_complete_or_na = (
                (mode_a != MODE_FULL_ARTIFACT or status_a == ARTIFACT_STATUS_COMPLETE)
                and (mode_b != MODE_FULL_ARTIFACT or status_b == ARTIFACT_STATUS_COMPLETE)
            )
            if both_complete_or_na:
                detail = _common(
                    reason="full_artifact_diff_not_implemented",
                    message=(
                        "Diff riga-per-riga da artifact full non ancora "
                        "implementato. Per ora possibile solo confronto "
                        "L1 (compare/l1) tramite hash aggregati."
                    ),
                    diff_available=False,
                )
                detail["artifact_status_a"] = status_a
                detail["artifact_status_b"] = status_b
                raise HTTPException(status_code=501, detail=detail)
            # almeno un lato pending/failed/missing
            detail = _common(
                reason="full_artifact_not_ready",
                message=(
                    "Diff non disponibile: artifact full non ancora "
                    "pronto (pending/failed) su almeno una delle due run."
                ),
                diff_available=False,
            )
            detail["artifact_status_a"] = status_a
            detail["artifact_status_b"] = status_b
            raise HTTPException(status_code=422, detail=detail)

        if MODE_METADATA_ONLY in (mode_a, mode_b):
            raise HTTPException(
                status_code=422,
                detail=_common(
                    reason="metadata_only_no_rows",
                    message=(
                        "Diff riga-per-riga non disponibile: almeno una "
                        "delle due run usa persistence_mode=metadata_only "
                        "(salva solo metadata + hash, niente node_rows). "
                        "Per change-detection L1: usa /compare/l1."
                    ),
                    diff_available=False,
                ),
            )

        # Mode mismatch fra full_pg e preview_only: il diff sarebbe
        # asimmetrico (full vs preview-cap) e darebbe falsi positivi
        # Rispondiamo conservativo invece di mascherarlo
        if {mode_a, mode_b} == {MODE_FULL_PG, MODE_PREVIEW_ONLY}:
            raise HTTPException(
                status_code=422,
                detail=_common(
                    reason="persistence_mode_mismatch",
                    message=(
                        "Diff non disponibile: le due run hanno "
                        "persistence_mode diversi (full_pg vs preview_only). "
                        "Il confronto L2 produrrebbe risultati asimmetrici. "
                        "Riallinea le run alla stessa mode oppure usa "
                        "/compare/l1 per change-detection via hash."
                    ),
                    diff_available=False,
                ),
            )

        # Solo full_pg+full_pg e preview_only+preview_only arrivano qui
        is_partial = (mode_a == MODE_PREVIEW_ONLY and mode_b == MODE_PREVIEW_ONLY)

        # Righe rimosse: in A ma non in B (confronto su row_hash
        # L'indice idx_node_rows_hash su (run_id, node_id, row_hash) rende
        # il NOT EXISTS O(n log m) anche per dataset grandi
        removed_raw = fetchall(
            """
            SELECT a.row_hash, a.row_data
            FROM node_rows a
            WHERE a.run_id = ? AND a.node_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM node_rows b
                WHERE b.run_id = ? AND b.node_id = ?
                  AND b.row_hash = a.row_hash
              )
            ORDER BY a.row_idx
            LIMIT 500
            """,
            (run_id_a, node_id, run_id_b, node_id),
        )

        # Righe aggiunte: in B ma non in A
        added_raw = fetchall(
            """
            SELECT b.row_hash, b.row_data
            FROM node_rows b
            WHERE b.run_id = ? AND b.node_id = ?
              AND NOT EXISTS (
                SELECT 1 FROM node_rows a
                WHERE a.run_id = ? AND a.node_id = ?
                  AND a.row_hash = b.row_hash
              )
            ORDER BY b.row_idx
            LIMIT 500
            """,
            (run_id_b, node_id, run_id_a, node_id),
        )

        # Conta totali senza limite
        row_a = fetchone(
            "SELECT COUNT(*) AS cnt FROM node_rows WHERE run_id = ? AND node_id = ?",
            (run_id_a, node_id),
        )
        row_b = fetchone(
            "SELECT COUNT(*) AS cnt FROM node_rows WHERE run_id = ? AND node_id = ?",
            (run_id_b, node_id),
        )
        count_a = row_a["cnt"] if row_a else 0
        count_b = row_b["cnt"] if row_b else 0

        # Shape del response identico a prima: gli elementi sono dict con
        # le colonne "piatte" della riga. Espandiamo row_data (jsonb) e
        # aggiungiamo _row_hash per retrocompatibilita' col frontend
        def _expand(r):
            data = r.get("row_data") or {}
            if isinstance(data, str):
                import json as _json
                data = _json.loads(data)
            return {"_row_hash": r.get("row_hash", ""), **data}

        removed_list = [_expand(r) for r in removed_raw]
        added_list = [_expand(r) for r in added_raw]

        # Sanitize NaN/+Inf/-Inf nelle righe espanse: i record possono
        # contenere sentinel JSONB ricostruiti come float speciali (vedi
        # `_from_jsonable_value` nei branch consumer di `node_rows`
        # Sul boundary HTTP non sono JSON-compliant. Bug #23 invariato
        removed_list = _sanitize_for_json(removed_list)
        added_list = _sanitize_for_json(added_list)

        response = {
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
            "persistence_mode_a": mode_a,
            "persistence_mode_b": mode_b,
            "diff_available": True,
            "partial": is_partial,
        }
        if is_partial:
            response["reason"] = "preview_only"
            response["message"] = (
                "Diff calcolato solo sulle righe preview persistite "
                "(persistence_mode=preview_only su entrambe le run). "
                "Cambi su righe oltre il preview cap NON sono rilevati."
            )
        return response
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore confronto L2 {run_id_a}/{run_id_b}: {e}", op="intermediate_api", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore confronto L2")


@router.post("/{run_id}/pin")
def pin_run(
    run_id: str,
    body: PinRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Pinna o spinna una run — le run pinnate sono escluse dalla retention FIFO."""
    try:
        # Stessa logica di `get_node_data`. Il superadmin puo' pinnare
        # run di qualsiasi gruppo (access check passa); senza questa
        # branch, `idb.pin_run` filtrerebbe per `current_user.group_id`
        # e fallirebbe con un falso 404 quando la run appartiene a un
        # altro gruppo. Quindi
        # superadmin → usa il group_id della run stessa (post-check
        # altri → group della run validato da _check_run_access
        run = _check_run_access(run_id, current_user)
        effective_group = (run or {}).get("group_id") or current_user.group_id
        ok = idb.pin_run(run_id, effective_group, pinned=body.pinned)
        if not ok:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.RUN_NOT_FOUND,
                    "message": f"Run {run_id} non trovata",
                },
            )
        return {"run_id": run_id, "pinned": body.pinned}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[GESTITO] Errore pin run {run_id}: {e}", op="intermediate_api", exc_info=True)
        raise HTTPException(status_code=500, detail="Errore pin run")
