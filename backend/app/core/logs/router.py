"""
Router API per la consultazione dei log — lettura da PostgreSQL.
"""

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from app.config import settings
from app.core.auth.auth import get_current_user, require_not_esecutore, CurrentUser, require_admin, group_filter
from app.db.pg_connection import fetchone as _fetchone, fetchall as _fetchall, execute as _execute
from app.shared.authz_helpers import assert_project_visible

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/logs", tags=["logs"])


def _row_to_dict(row: dict) -> dict:
    d = dict(row) if row else {}
    # Deserializza details JSON. Se il campo non e' JSON valido lasciamolo
    # come stringa: log di sistema legacy possono avere `details` testuali.
    if d.get("details"):
        try:
            d["details"] = json.loads(d["details"])
        except (ValueError, TypeError) as e:
            logger.debug(
                "details non-JSON su log row, lascio come stringa: %s: %s",
                type(e).__name__,
                e,
            )
    # Rimuovi campi vuoti
    return {k: v for k, v in d.items() if v not in (None, "", [])}


@router.get("")
def get_logs(
    level:      Optional[str] = Query(None, description="Filtro livelli (comma-separated: INFO,WARNING,ERROR)"),
    module:     Optional[str] = Query(None, description="Filtro moduli (comma-separated: estrai,manipola,...)"),
    op:         Optional[str] = Query(None, description="Filtro operazioni"),
    project_id: Optional[str] = Query(None, description="Filtro per project_id"),
    search:     Optional[str] = Query(
        None,
        max_length=100,
        description="Ricerca substring nel messaggio (max 100 char). "
                    "LIKE '%...%' non usa indici — se il volume cresce "
                    "valutare indice trigram (pg_trgm) o full-text.",
    ),
    after:      Optional[str] = Query(None, description="Timestamp minimo (ISO format)"),
    before:     Optional[str] = Query(None, description="Timestamp massimo (ISO format)"),
    current_user: CurrentUser = Depends(require_not_esecutore),
    limit:  int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
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
        # N6 (2026-04-17): fail-closed ownership check prima di filtrare per
        # project_id. Pre-fix il filtro `group_id = X OR group_id = ''` a
        # riga ~82 bloccava il cross-tenant per log applicativi, ma i log
        # di sistema con group_id='' erano leak-able da utenti cross-tenant
        # che conoscevano un project_id di un altro gruppo. Ora rifiutiamo
        # esplicitamente l'accesso a project altrui (superadmin bypassa
        # via assert_project_visible che accetta anche progetti condivisi).
        if current_user.role != "superadmin":
            assert_project_visible(project_id, current_user)
        conditions.append("project_id = ?")
        params.append(project_id)

    if search:
        # Escape dei metacaratteri LIKE (% e _) e del backslash stesso.
        # Senza escape, un utente potrebbe usare `%` o `_` per costruire
        # pattern wildcard arbitrari (es. cercare `%password%` per discovery).
        # PostgreSQL supporta `ESCAPE '\'` per l'escape esplicito.
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

    total_row = _fetchone(f"SELECT COUNT(*) as c FROM logs {where}", tuple(params))
    total = total_row["c"] if total_row else 0
    rows = _fetchall(
        f"SELECT * FROM logs {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset)
    )

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
    conditions = []
    params = []
    if after:
        conditions.append("ts >= ?")
        params.append(after)
    if current_user.role != "superadmin":
        conditions.append("(group_id = ? OR group_id = '')")
        params.append(current_user.group_id)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    p = tuple(params)

    total_row = _fetchone(f"SELECT COUNT(*) as c FROM logs {where}", p)
    total = total_row["c"] if total_row else 0

    by_level = {r["level"]: r["cnt"] for r in _fetchall(
        f"SELECT level, COUNT(*) as cnt FROM logs {where} GROUP BY level", p
    )}
    by_module = {r["module"]: r["cnt"] for r in _fetchall(
        f"SELECT module, COUNT(*) as cnt FROM logs {where} GROUP BY module ORDER BY cnt DESC LIMIT 20", p
    )}
    op_where = f"{where} AND op != ''" if where else "WHERE op != ''"
    by_op = {r["op"]: r["cnt"] for r in _fetchall(
        f"SELECT op, COUNT(*) as cnt FROM logs {op_where} GROUP BY op ORDER BY cnt DESC LIMIT 20", p
    )}
    err_where = f"{where} AND level = 'ERROR'" if where else "WHERE level = 'ERROR'"
    recent_errors = [_row_to_dict(r) for r in _fetchall(
        f"SELECT * FROM logs {err_where} ORDER BY id DESC LIMIT 10", p
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
    if current_user.role == "superadmin":
        _execute("DELETE FROM logs")
        _execute("DELETE FROM log_runs")
    else:
        _execute("DELETE FROM logs WHERE group_id = ? OR group_id = ''", (current_user.group_id,))
        _execute("DELETE FROM log_runs WHERE group_id = ?", (current_user.group_id,))
    return {"status": "ok", "message": "Log e run svuotati"}
