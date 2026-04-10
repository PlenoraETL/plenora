"""
Router API per la consultazione dei log — lettura da SQLite (log.db).

Endpoints:
- GET  /api/logs          → log paginati con filtri
- GET  /api/logs/stats    → statistiche aggregate
- POST /api/logs/clear    → svuota log
"""

import json
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Query
from app.config import settings
from app.core.auth.auth import get_current_user, require_not_esecutore, CurrentUser, require_admin, group_filter

router = APIRouter(prefix="/logs", tags=["logs"])

LOG_DB_PATH = settings.LOGS_DIR / "log.db"


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(LOG_DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Deserializza details JSON
    if d.get("details"):
        try:
            d["details"] = json.loads(d["details"])
        except Exception:
            pass
    # Rimuovi campi vuoti
    return {k: v for k, v in d.items() if v not in (None, "", [])}


@router.get("")
def get_logs(
    level:      Optional[str] = Query(None, description="Filtro livelli (comma-separated: INFO,WARNING,ERROR)"),
    module:     Optional[str] = Query(None, description="Filtro moduli (comma-separated: estrai,manipola,...)"),
    op:         Optional[str] = Query(None, description="Filtro operazioni"),
    project_id: Optional[str] = Query(None, description="Filtro per project_id"),
    search:     Optional[str] = Query(None, description="Ricerca full-text nel messaggio"),
    after:      Optional[str] = Query(None, description="Timestamp minimo (ISO format)"),
    before:     Optional[str] = Query(None, description="Timestamp massimo (ISO format)"),
    current_user: CurrentUser = Depends(require_not_esecutore),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    if not LOG_DB_PATH.exists():
        return {"entries": [], "total": 0, "limit": limit, "offset": offset, "has_more": False}

    conditions = []
    params = []

    if level:
        placeholders = ",".join("?" * len(level.split(",")))
        conditions.append(f"level IN ({placeholders})")
        params.extend(l.strip().upper() for l in level.split(","))

    if module:
        placeholders = ",".join("?" * len(module.split(",")))
        conditions.append(f"module IN ({placeholders})")
        params.extend(m.strip().lower() for m in module.split(","))

    if op:
        placeholders = ",".join("?" * len(op.split(",")))
        conditions.append(f"op IN ({placeholders})")
        params.extend(o.strip().lower() for o in op.split(","))

    if project_id:
        conditions.append("project_id = ?")
        params.append(project_id)

    if search:
        # Escape dei metacaratteri LIKE (% e _) e del backslash stesso.
        # Stesso fix di logs/router.py — qui era stato omesso.
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conditions.append("msg LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped}%")

    if after:
        conditions.append("ts >= ?")
        params.append(after)

    if before:
        conditions.append("ts <= ?")
        params.append(before)

    # Filtro per gruppo (superadmin vede tutto, altri solo il proprio gruppo)
    if current_user.role != "superadmin":
        conditions.append("(group_id = ? OR group_id = '')")
        params.append(current_user.group_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with _get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM logs {where}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
            params + [limit, offset]
        ).fetchall()

    entries = [_row_to_dict(r) for r in rows]

    return {
        "entries": entries,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + limit < total,
    }


@router.get("/stats")
def get_log_stats(
    after: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    if not LOG_DB_PATH.exists():
        return {"total": 0, "by_level": {}, "by_module": {}, "by_operation": {}, "recent_errors": []}

    where = "WHERE ts >= ?" if after else ""
    params = [after] if after else []

    with _get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM logs {where}", params).fetchone()[0]

        by_level = {r["level"]: r["cnt"] for r in conn.execute(
            f"SELECT level, COUNT(*) as cnt FROM logs {where} GROUP BY level", params
        )}

        by_module = {r["module"]: r["cnt"] for r in conn.execute(
            f"SELECT module, COUNT(*) as cnt FROM logs {where} GROUP BY module ORDER BY cnt DESC LIMIT 20", params
        )}

        _conn = "AND" if where else "WHERE"
        by_op = {r["op"]: r["cnt"] for r in conn.execute(
            f"SELECT op, COUNT(*) as cnt FROM logs {where} {_conn} op != '' GROUP BY op ORDER BY cnt DESC LIMIT 20", params
        )}

        _conn2 = "AND" if where else "WHERE"
        recent_errors = [_row_to_dict(r) for r in conn.execute(
            f"SELECT * FROM logs {where} {_conn2} level = 'ERROR' ORDER BY id DESC LIMIT 10",
            params
        )]

    return {
        "total": total,
        "by_level": by_level,
        "by_module": by_module,
        "by_operation": by_op,
        "recent_errors": recent_errors,
    }


@router.post("/clear")
def clear_logs(current_user: CurrentUser = Depends(require_admin)):
    """
    Svuota i log e le run.
    superadmin → svuota tutti i log e tutte le run
    admin      → svuota solo i log e le run del proprio gruppo
    """
    if LOG_DB_PATH.exists():
        with _get_conn() as conn:
            if current_user.role == "superadmin":
                conn.execute("DELETE FROM logs")
                conn.execute("DELETE FROM runs")
            else:
                conn.execute("DELETE FROM logs WHERE group_id = ? OR group_id = ''", (current_user.group_id,))
                conn.execute("DELETE FROM runs WHERE group_id = ?", (current_user.group_id,))
            conn.commit()
    return {"status": "ok", "message": "Log e run svuotati"}
