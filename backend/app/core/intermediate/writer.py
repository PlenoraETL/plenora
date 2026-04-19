"""
Intermediate Writer — logica di scrittura per SQLite #3.

Responsabilità:
- Calcolo hash aggregato per run (input_hash / output_hash)
- Calcolo _row_hash MD5 per ogni riga
- Orchestrazione scrittura nodo completo (schema + righe + node_meta)
"""

import hashlib
from typing import Dict, List, Any, Optional

from app.utils.logger import get_logger
from app.core.intermediate import db

logger = get_logger(__name__)


# ============================================================
# HASH AGGREGATO (opzione 2) per input_hash / output_hash
# ============================================================

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


# ============================================================
# ROW HASH MD5 per _row_hash
# ============================================================

def _format_float_stable(v: float) -> str:
    """Formatta un float in modo stabile per hashing (N10).

    Strategia: 6 decimali fissi, strip zeri trailing + punto.
    Esempi:
        1.23           -> "1.23"
        1.2300000001   -> "1.23"        (coerente con 1.23 — dedup ok)
        1.0            -> "1"           (coerente con int 1)
        0.0            -> "0"
        1.234567891    -> "1.234568"    (tronca a 6 decimali)
        -1.23          -> "-1.23"

    NaN/Inf gestiti esplicitamente (f"{nan:.6f}" darebbe "nan", ma
    meglio output espliciti per leggibilita' nei log).
    """
    import math
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
    import decimal
    if isinstance(v, decimal.Decimal):
        # N10: Decimal -> float -> formato stabile. Prima era str(float(v))
        # che e' sensibile a floating-point noise.
        return _format_float_stable(float(v))
    if isinstance(v, (bytes, bytearray)):
        return f"bytes:{len(v)}"
    # Geometrie Shapely -> WKT (deve venire PRIMA dei check numerici per evitare
    # che "int" in "Point" / "MultiPoint" triggeri il branch sbagliato)
    try:
        from shapely.geometry.base import BaseGeometry
        if isinstance(v, BaseGeometry):
            return f"geom:{v.wkt}"
    except ImportError:
        pass
    # Usiamo isinstance invece di substring su __name__ per evitare falsi positivi
    # es. "int" in "Point" -> True causava TypeError: int() on a Point object
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        # N10 (2026-04-17): format stabile via helper condiviso. Prima
        # era str(v) crudo che rompeva la dedup su floating-point noise
        # (1.23 vs 1.2300000001 producevano hash diversi).
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


# ============================================================
# SCRITTURA NODO COMPLETO
# ============================================================

def write_node(run_id: str, node_id: str, node_type: str,
               rows: List[Dict], col_schema: dict,
               exec_time: float, status: str,
               error_msg: str = None) -> str:
    """
    Scrive un nodo completo in SQLite #3:
    1. Crea tabella dinamica
    2. Calcola _row_hash per ogni riga
    3. Bulk insert
    4. Scrive node_meta
    5. Ritorna hash aggregato della tabella (per input/output_hash)

    Returns: hash aggregato delle righe del nodo
    """
    try:
        if status == "ok" and rows:
            # Calcola _row_hash per ogni riga. Non serve piu' sanitizzare
            # list/dict/geometry qui: db.insert_node_rows usa _to_jsonable
            # che gestisce tutti i tipi per la serializzazione in JSONB.
            col_names_sorted = sorted(col_schema.keys())
            rows_with_hash = [
                {"_row_hash": compute_row_hash(row, col_names_sorted), **row}
                for row in rows
            ]

            # Bulk insert nella tabella unificata plenora.node_rows
            db.insert_node_rows(run_id, node_id, rows_with_hash, col_schema)

            # Hash aggregato per questo nodo
            node_hash = compute_aggregate_hash(rows, col_schema)
        else:
            node_hash = hashlib.sha256(b"empty").hexdigest()

        # Scrive node_meta
        db.upsert_node_meta(
            run_id=run_id,
            node_id=node_id,
            node_type=node_type,
            row_count=len(rows) if rows else 0,
            col_schema=col_schema,
            exec_time=exec_time,
            status=status,
            error_msg=error_msg,
        )

        logger.info(
            f"Nodo scritto: run={run_id} node={node_id} "
            f"rows={len(rows) if rows else 0} status={status}",
            op="intermediate_write"
        )
        return node_hash

    except Exception as e:
        logger.error(
            f"Errore scrittura nodo {node_id} run {run_id}: {e}",
            op="intermediate_write",
            exc_info=True
        )
        # Scrive comunque node_meta con errore
        try:
            db.upsert_node_meta(
                run_id=run_id,
                node_id=node_id,
                node_type=node_type,
                row_count=0,
                exec_time=exec_time,
                status="error",
                error_msg=str(e),
            )
        except Exception:
            pass
        return hashlib.sha256(b"error").hexdigest()


def close_run(run_id: str, input_hash: str, output_hash: str,
              total_exec_time: float, success: bool) -> None:
    """
    Chiude una run aggiornando runs con i dati finali.
    """
    try:
        # Conta nodi per status
        node_metas = db.list_node_meta(run_id)
        nodes_ok = sum(1 for n in node_metas if n.get("status") == "ok")
        nodes_error = sum(1 for n in node_metas if n.get("status") == "error")
        nodes_skipped = sum(1 for n in node_metas if n.get("status") == "skipped")

        # Righe in/out dai nodi primo e ultimo
        rows_in = node_metas[0].get("row_count", 0) if node_metas else 0
        rows_out = node_metas[-1].get("row_count", 0) if node_metas else 0

        db.update_run(
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

        # N8 (2026-04-17): applica retention FIFO subito dopo chiusura run.
        # Pre-fix: apply_runs_retention era definita ma mai chiamata da
        # alcun callsite -> intermediate.runs + node_meta + node_rows
        # crescevano indefinitamente causando disk fill nel tempo.
        # Ora retention FIFO (N run piu' recenti non pinnate, N da
        # projects.retention_n o default 5) viene applicata a ogni
        # chiusura run. Best-effort: un errore qui non rollbacka l'update.
        try:
            run = db.get_run(run_id)
            if run and run.get("canvas_id"):
                db.apply_runs_retention(run["canvas_id"], run.get("group_id", ""))
        except Exception as _re:
            logger.warning(
                f"[N8] Retention intermediate fallita (non bloccante) "
                f"per run {run_id}: {_re}",
                op="intermediate_write",
            )

    except Exception as e:
        logger.error(f"[GESTITO] Errore chiusura run {run_id}: {e}", op="intermediate_write")
        try:
            db.update_run(run_id, status="failed")
        except Exception:
            pass
