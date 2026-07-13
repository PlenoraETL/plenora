"""
Intermediate Writer — logica di scrittura nello storage intermediate

"""

import decimal
import math

import hashlib
from typing import Dict, List

from app.core.intermediate import node_snapshots_db, retention, runs_db
from app.core.intermediate.exceptions import IntermediateWriteError
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _resolve_runs_retention_n(retention_n: int | None = None) -> int:
    """Normalize caller-provided retention policy for intermediate cleanup."""
    if retention_n is None:
        return retention.DEFAULT_RUNS_RETENTION
    return retention_n


# HASH AGGREGATO (opzione 2) per input_hash / output_hash

def compute_aggregate_hash(rows: List[Dict], col_schema: dict) -> str:
    """
    Calcola hash aggregato su un dataset.

    Algoritmo (opzione 2 — per colonna, ordinato per nome colonna):
    - Colonne numeriche (int/float): count non-null, sum, min, max
    - Colonne testuali (str): count non-null, count distinti,
      hash valori distinti ordinati alfabeticamente
    - Colonne geometriche (geometry/geom/wkt/wkb nel nome):
      count non-null, count non-vuoti
    - Concatenazione ordinata per nome colonna -> SHA256

    Limitazione nota: scambio simmetrico di valori non viene rilevato.
    """
    if not rows or not col_schema:
        return hashlib.sha256(b"empty").hexdigest()

    col_names_sorted = sorted(col_schema.keys())
    parts = []

    for col in col_names_sorted:
        dtype = str(col_schema.get(col, "object")).lower()
        values = [r.get(col) for r in rows]
        non_null = [v for v in values if v is not None]

        col_lower = col.lower()
        is_geom = any(k in col_lower for k in ("geom", "geometry", "wkt", "wkb", "shape"))

        if is_geom:
            non_empty = [v for v in non_null if v and str(v).strip() not in ("", "null")]
            parts.append(f"{col}:geom:count={len(non_null)}:valid={len(non_empty)}")

        elif "int" in dtype or "float" in dtype:
            nums = []
            for v in non_null:
                try:
                    nums.append(float(v))
                except (TypeError, ValueError):
                    pass
            if nums:
                parts.append(
                    f"{col}:num:count={len(non_null)}:"
                    f"sum={sum(nums):.6f}:"
                    f"min={min(nums):.6f}:"
                    f"max={max(nums):.6f}"
                )
            else:
                parts.append(f"{col}:num:count={len(non_null)}:empty")

        else:
            strs = [str(v) for v in non_null]
            distinct = sorted(set(strs))
            distinct_hash = hashlib.md5(
                "|".join(distinct).encode("utf-8", errors="replace")
            ).hexdigest()
            parts.append(
                f"{col}:str:count={len(non_null)}:"
                f"distinct={len(distinct)}:"
                f"dhash={distinct_hash}"
            )

    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


# ROW HASH MD5 per _row_hash

def _format_float_stable(v: float) -> str:
    """Formatta un float in modo stabile per hashing (N10).

    Strategia: 6 decimali fissi, strip zeri trailing + punto.
    Esempi:
        1.23 -> "1.23"
        1.2300000001 -> "1.23" (coerente con 1.23 — dedup ok)
        1.0 -> "1" (coerente con int 1)
        0.0 -> "0"
        1.234567891 -> "1.234568" (tronca a 6 decimali)
        -1.23 -> "-1.23"

    NaN/Inf gestiti esplicitamente (f"{nan:.6f}" darebbe "nan", ma
    meglio output espliciti per leggibilita' nei log).
    """
    if math.isnan(v):
        return "nan"
    if math.isinf(v):
        return "+inf" if v > 0 else "-inf"
    return f"{v:.6f}".rstrip("0").rstrip(".")


def _normalize_value(v) -> str:
    """Normalizza un valore per l'hashing — deterministico su tutti i tipi."""
    if v is None:
        return "None"
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        # N10: Decimal -> float -> formato stabile. Prima era str(float(v
        # che e' sensibile a floating-point noise
        return _format_float_stable(float(v))
    if isinstance(v, (bytes, bytearray)):
        return f"bytes:{len(v)}"
    # Geometrie Shapely -> WKT (deve venire PRIMA dei check numerici per evitare
    # che "int" in "Point" / "MultiPoint" triggeri il branch sbagliato
    try:
        from shapely.geometry.base import BaseGeometry
        if isinstance(v, BaseGeometry):
            return f"geom:{v.wkt}"
    except ImportError:
        pass
    # Usiamo isinstance invece di substring su __name__ per evitare falsi positivi
    # es. "int" in "Point" -> True causava TypeError: int on a Point object
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # Format stabile: limita il rumore floating-point nella dedup
        # (es. 1.23 vs 1.2300000001).
        return _format_float_stable(v)
    return str(v)


def compute_row_hash(row: Dict, col_names_sorted: List[str]) -> str:
    """
    Calcola MD5 di una riga basato su tutti i valori ordinati per nome colonna.
    Deterministico — indipendente dall'ordine delle colonne nel dict.
    """
    parts = []
    for col in col_names_sorted:
        val = row.get(col)
        parts.append(f"{col}={_normalize_value(val)}")
    return hashlib.md5("|".join(parts).encode("utf-8", errors="replace")).hexdigest()


# SCRITTURA NODO COMPLETO

def write_node(run_id: str, node_id: str, node_type: str,
               rows: List[Dict], col_schema: dict,
               exec_time: float, status: str,
               error_msg: str = None) -> str:
    """
    Scrive un nodo completo nello storage intermediate (PostgreSQL):
    1. Crea tabella dinamica
    2. Calcola _row_hash per ogni riga
    3. Bulk insert
    4. Scrive node_meta
    5. Ritorna hash aggregato della tabella (per input/output_hash)

    Returns: hash aggregato delle righe del nodo
    """
    try:
        node_hash = (
            compute_aggregate_hash(rows, col_schema)
            if status == "ok" and rows
            else hashlib.sha256(b"empty").hexdigest()
        )
        node_snapshots_db.upsert_node_meta_extended(
            run_id=run_id,
            node_id=node_id,
            node_type=node_type,
            row_count=len(rows) if rows else 0,
            col_schema=col_schema,
            exec_time=exec_time,
            status=status,
            error_msg=error_msg,
            node_hash=node_hash,
            hash_algo="aggregate_sha256_v1",
            persistence_mode="metadata_only",
            persisted_rows_count=0,
            preview_truncated=False,
            artifact_status="none",
        )

        logger.info(
            f"Nodo scritto: run={run_id} node={node_id} "
            f"rows={len(rows) if rows else 0} status={status}",
            op="intermediate_write"
        )
        return node_hash

    # Resource guard: fallback upsert_node_meta(status="error") + re-raise
    # tipizzato via IntermediateWriteError. Non generare hash sintetici:
    # contaminerebbero input_hash/output_hash e l'invariante di determinismo
    # di dedup/replay.
    # outcome: fail-closed tipizzato
    except Exception as e:
        logger.error(
            f"[CRASH] Errore scrittura nodo {node_id} run {run_id}: {e}",
            op="intermediate_write",
            exc_info=True,
        )
        # Scrive comunque node_meta con errore (resource guard best-effort
        try:
            node_snapshots_db.upsert_node_meta(
                run_id=run_id,
                node_id=node_id,
                node_type=node_type,
                row_count=0,
                exec_time=exec_time,
                status="error",
                error_msg=str(e),
            )
        except Exception as _cleanup_err:
            logger.debug(
                f"upsert_node_meta fallback fallito per run {run_id} node {node_id}: "
                f"{type(_cleanup_err).__name__}: {_cleanup_err}",
                op="intermediate_write",
            )
        raise IntermediateWriteError(
            run_id=run_id, node_id=node_id, original_error=e,
        ) from e


def close_run(run_id: str, input_hash: str, output_hash: str,
              total_exec_time: float, success: bool,
              retention_n: int | None = None) -> None:
    """
    Chiude una run aggiornando runs con i dati finali.
    """
    try:
        # Conta nodi per status
        node_metas = node_snapshots_db.list_node_meta(run_id)
        nodes_ok = sum(1 for n in node_metas if n.get("status") == "ok")
        nodes_error = sum(1 for n in node_metas if n.get("status") == "error")
        nodes_skipped = sum(1 for n in node_metas if n.get("status") == "skipped")

        # Righe in/out dai nodi primo e ultimo
        rows_in = node_metas[0].get("row_count", 0) if node_metas else 0
        rows_out = node_metas[-1].get("row_count", 0) if node_metas else 0

        runs_db.update_run(
            run_id,
            status="completed" if success else "failed",
            input_hash=input_hash,
            output_hash=output_hash,
            total_exec_time=total_exec_time,
            total_rows_in=rows_in,
            total_rows_out=rows_out,
            total_rows_dropped=max(0, rows_in - rows_out),
            nodes_ok=nodes_ok,
            nodes_error=nodes_error,
            nodes_skipped=nodes_skipped,
        )

        logger.info(
            f"Run chiusa: {run_id} status={'completed' if success else 'failed'} "
            f"nodes={len(node_metas)} rows_in={rows_in} rows_out={rows_out}",
            op="intermediate_write"
        )

        # Applica retention FIFO subito dopo chiusura run per evitare crescita
        # indefinita di intermediate.runs + node_meta + node_rows.
        # Retention FIFO: N run piu' recenti non pinnate, N da
        # projects.retention_n passato dagli orchestration edge, o default 5)
        # viene applicata a ogni chiusura run. Best-effort: un errore qui non
        # rollbacka l'update
        try:
            run = runs_db.get_run(run_id)
            if run and run.get("canvas_id"):
                canvas_id = run["canvas_id"]
                retention.apply_runs_retention(
                    canvas_id,
                    run.get("group_id", ""),
                    retention_n=_resolve_runs_retention_n(retention_n),
                )
        except Exception as _re:
            logger.warning(
                f"Retention intermediate fallita (non bloccante) "
                f"per run {run_id}: {_re}",
                op="intermediate_write",
            )

    # Resource guard: cleanup best-effort con fallback update_run(status="failed")
    # e re-raise dell'eccezione originale. Il caller decide il proprio flusso;
    # il task Celery top-level cattura gia' attorno a idb_close_run.
    except Exception as e:
        logger.error(
            f"[CRASH] Errore chiusura run {run_id}: {e}",
            op="intermediate_write",
            exc_info=True,
        )
        try:
            runs_db.update_run(run_id, status="failed")
        except Exception as _cleanup_err:
            logger.debug(
                f"update_run(status=failed) fallback fallito per run {run_id}: "
                f"{type(_cleanup_err).__name__}: {_cleanup_err}",
                op="intermediate_write",
            )
        raise
