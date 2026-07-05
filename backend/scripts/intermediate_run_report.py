#!/usr/bin/env python3
"""Report read-only per una `run_id`."""

from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List, Optional


def _format_bool(v: Optional[bool]) -> str:
    if v is None:
        return "-"
    return "Y" if v else "N"


def _format_mode(v: Optional[str]) -> str:
    if v is None or v == "":
        return "full_pg(default)"
    return v


def _format_int(v: Optional[int]) -> str:
    return "-" if v is None else f"{int(v):,}"


def _fetch_run(run_id: str) -> Optional[Dict[str, Any]]:
    from app.db.pg_connection import fetchone
    return fetchone(
        """
        SELECT run_id, canvas_id, group_id, status, timestamp,
               total_rows_in, total_rows_out, total_nodes,
               nodes_ok, nodes_error, nodes_skipped, total_exec_time,
               input_hash, output_hash
        FROM runs
        WHERE run_id = ?
        """,
        (run_id,),
    )


def _fetch_node_meta(run_id: str) -> List[Dict[str, Any]]:
    from app.db.pg_connection import fetchall
    return fetchall(
        """
        SELECT node_id, node_type, status, row_count, exec_time,
               node_hash, hash_algo, persistence_mode,
               persisted_rows_count, preview_truncated,
               artifact_uri, artifact_status
        FROM node_meta
        WHERE run_id = ?
        ORDER BY node_id
        """,
        (run_id,),
    )


def _count_rows_for_node(run_id: str, node_id: str) -> int:
    from app.db.pg_connection import fetchone
    row = fetchone(
        "SELECT COUNT(*) AS n FROM node_rows WHERE run_id = ? AND node_id = ?",
        (run_id, node_id),
    )
    return int(row["n"]) if row else 0


def _bytes_for_node(run_id: str, node_id: str) -> Optional[int]:
    """Somma `pg_column_size(row_data)` per il nodo. None se la query
    fallisce (privilegi/tipo non disponibile)."""
    from app.db.pg_connection import fetchone
    try:
        row = fetchone(
            """
            SELECT COALESCE(SUM(pg_column_size(row_data)), 0) AS b
            FROM node_rows
            WHERE run_id = ? AND node_id = ?
            """,
            (run_id, node_id),
        )
        return int(row["b"]) if row else 0
    except Exception:
        return None


def _human_bytes(n: Optional[int]) -> str:
    if n is None:
        return "-"
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def report(run_id: str) -> int:
    run = _fetch_run(run_id)
    if not run:
        print(f"ERROR: run_id={run_id!r} non trovata in plenora.runs",
              file=sys.stderr)
        return 2

    print(f"== Run {run_id} ==")
    print(f"canvas_id={run.get('canvas_id')!r} status={run.get('status')!r} "
          f"timestamp={run.get('timestamp')}")
    print(f"total_nodes={_format_int(run.get('total_nodes'))} "
          f"ok={_format_int(run.get('nodes_ok'))} "
          f"err={_format_int(run.get('nodes_error'))} "
          f"skipped={_format_int(run.get('nodes_skipped'))} "
          f"exec_s={run.get('total_exec_time')}")
    print(f"input_hash={run.get('input_hash') or '-'}")
    print(f"output_hash={run.get('output_hash') or '-'}")
    print()

    metas = _fetch_node_meta(run_id)
    if not metas:
        print("Nessun record node_meta per questa run.")
        return 0

    headers = (
        "node_id", "type", "mode",
        "row_count", "persisted",
        "trunc", "hash?", "actual_rows", "jsonb_bytes",
    )
    rows: List[tuple] = [headers]
    total_actual = 0
    total_bytes = 0
    bytes_known = False
    for m in metas:
        nid = m.get("node_id") or "-"
        actual = _count_rows_for_node(run_id, nid)
        total_actual += actual
        size = _bytes_for_node(run_id, nid) if actual > 0 else 0
        if size is not None:
            total_bytes += size
            bytes_known = True
        rows.append((
            nid,
            m.get("node_type") or "-",
            _format_mode(m.get("persistence_mode")),
            _format_int(m.get("row_count")),
            _format_int(m.get("persisted_rows_count")),
            _format_bool(m.get("preview_truncated")),
            "Y" if m.get("node_hash") else "N",
            _format_int(actual),
            _human_bytes(size),
        ))

    # Calcola larghezza colonne.
    widths = [max(len(str(r[i])) for r in rows) for i in range(len(headers))]
    for r in rows:
        line = "  ".join(str(c).ljust(w) for c, w in zip(r, widths))
        print(line)
    print()

    # Drift detection: persisted_rows_count != actual rows in node_rows.
    drift = []
    for m in metas:
        declared = m.get("persisted_rows_count")
        actual = _count_rows_for_node(run_id, m["node_id"])
        if declared is not None and int(declared) != actual:
            drift.append((m["node_id"], declared, actual))
    if drift:
        print("WARN drift persisted_rows_count vs actual:")
        for nid, declared, actual in drift:
            print(f"  {nid}: declared={declared} actual={actual}")
        print()

    print(f"Totale righe persistite (tutti nodi): {total_actual:,}")
    if bytes_known:
        print(f"Totale jsonb bytes (approx): {_human_bytes(total_bytes)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report read-only per una run intermediate.",
    )
    parser.add_argument(
        "run_id",
        help="run_id (UUID) della run da ispezionare",
    )
    args = parser.parse_args()
    try:
        return report(args.run_id)
    except KeyboardInterrupt:
        return 130
    except Exception as e:
        print(f"ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
