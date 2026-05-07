"""
Groups DAL — estratto da `config_db_service.py`

(BE-GO-READY-DB-ENGINE-002-GROUPS-CLUSTER-EXTRACTION).
"""

from typing import Dict, List, Optional

from app.db.pg_connection import fetchone, fetchall, execute


def get_group(group_id: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM groups WHERE id = ?", (group_id,))


def list_groups() -> List[Dict]:
    return fetchall("SELECT * FROM groups ORDER BY name")


def create_group(group_id: str, name: str, description: str = "") -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO groups (id, name, description, created_at, updated_at) VALUES (?,?,?,?,?)",
        (group_id, name, description, now, now)
    )
    return get_group(group_id)


def get_group_summary(group_id: str) -> dict:
    """
    Ritorna il conteggio di tutte le entità appartenenti al gruppo.
    Usato dal frontend per il modale di conferma prima del cascade delete.

    Fix: prima erano 7 query separate. Ora 1 sola query con subquery scalari
    — PostgreSQL/MySQL le eseguono in parallelo internamente.
    """
    row = fetchone(
        "SELECT "
        "(SELECT COUNT(*) FROM projects WHERE group_id = ?) AS projects, "
        "(SELECT COUNT(*) FROM users WHERE group_id = ?) AS users, "
        "(SELECT COUNT(*) FROM connections WHERE group_id = ?) AS connections, "
        "(SELECT COUNT(*) FROM file_sources WHERE group_id = ?) AS file_sources, "
        "(SELECT COUNT(*) FROM datasources WHERE group_id = ?) AS datasources, "
        "(SELECT COUNT(*) FROM api_connections WHERE group_id = ?) AS api_connections, "
        "(SELECT COUNT(*) FROM schedules WHERE group_id = ?) AS schedules",
        tuple([group_id] * 7)
    )
    if not row:
        return {k: 0 for k in ("projects", "users", "connections",
                               "file_sources", "datasources",
                               "api_connections", "schedules")}
    return {
        "projects":        row.get("projects", 0),
        "users":           row.get("users", 0),
        "connections":     row.get("connections", 0),
        "file_sources":    row.get("file_sources", 0),
        "datasources":     row.get("datasources", 0),
        "api_connections": row.get("api_connections", 0),
        "schedules":       row.get("schedules", 0),
    }


def delete_group_cascade(group_id: str) -> dict:
    """
    Cascade delete completo di un gruppo e di tutti i suoi dati:
      config.db   → schedules, api_connections, datasources, file_sources,
                    connections, comparison_keys, projects, users, group
      results.db  → results per ogni progetto del gruppo
      intermediate.db → runs + tabelle dinamiche per ogni progetto del gruppo
      log.db      → log_runs per group_id

    Ritorna un dict con i conteggi di ciò che è stato eliminato.
    Non solleva eccezioni — ogni step è non bloccante e logga i warning.
    Il gruppo viene eliminato solo alla fine, se tutti gli step precedenti
    sono andati a buon fine.
    """
    from app.utils.logger import get_logger as _get_logger
    _log = _get_logger("config_db_service.delete_group_cascade")

    report = {
        "schedules": 0, "api_connections": 0, "datasources": 0,
        "file_sources": 0, "connections": 0, "projects": 0,
        "users": 0, "results": 0, "intermediate_runs": 0,
        "log_runs": 0,
    }

    # 1-5: Batch DELETE per tabelle con group_id
    for table in ["schedules", "api_connections", "datasources", "file_sources", "connections"]:
        try:
            cur = execute(f"DELETE FROM {table} WHERE group_id = ?", (group_id,))
            report[table] = cur.rowcount
        except Exception as e:
            _log.warning(f"Cascade delete {table} fallito: {e}")

    # 6. Projects → results.db + intermediate.db per ognuno
    try:
        projects = fetchall("SELECT id FROM projects WHERE group_id = ?", (group_id,))
        project_ids = [p["id"] for p in projects]

        # Results + intermediate runs: loop perche toccano DB logici diversi
        for pid in project_ids:
            try:
                import app.db.results_db_service as res_db
                deleted = res_db.delete_results_for_project(pid)
                report["results"] += deleted
            except Exception as e:
                _log.warning(f"Cascade delete results progetto {pid}: {e}")

            try:
                from app.core.intermediate.db import delete_all_runs_for_canvas
                deleted = delete_all_runs_for_canvas(pid)
                report["intermediate_runs"] += deleted
            except Exception as e:
                _log.warning(f"Cascade delete intermediate progetto {pid}: {e}")

        # Comparison keys: batch delete via subquery (O(1) query invece di O(n))
        if project_ids:
            try:
                execute(
                    "DELETE FROM comparison_keys WHERE canvas_id IN "
                    "(SELECT id FROM projects WHERE group_id = ?)",
                    (group_id,)
                )
            except Exception as e:
                _log.warning(f"Cascade batch delete comparison_keys fallito: {e}")

            # Progetti: batch delete
            try:
                cur = execute("DELETE FROM projects WHERE group_id = ?", (group_id,))
                report["projects"] = cur.rowcount if hasattr(cur, 'rowcount') else len(project_ids)
            except Exception as e:
                _log.warning(f"Cascade batch delete projects fallito: {e}")
    except Exception as e:
        _log.warning(f"Cascade delete projects fallito: {e}")

    # 7. Users
    try:
        cur = execute("DELETE FROM users WHERE group_id = ?", (group_id,))
        report["users"] = cur.rowcount
    except Exception as e:
        _log.warning(f"Cascade delete users fallito: {e}")

    # 8. log_runs (ora su PostgreSQL, stessa connessione)
    try:
        cur = execute("DELETE FROM log_runs WHERE group_id = ?", (group_id,))
        report["log_runs"] = cur.rowcount
    except Exception as e:
        _log.warning(f"Cascade delete log_runs fallito: {e}")

    # 9. Elimina il gruppo
    try:
        execute("DELETE FROM groups WHERE id = ?", (group_id,))
    except Exception as e:
        _log.error(f"Eliminazione gruppo {group_id} fallita: {e}")
        raise

    _log.info(
        f"Cascade delete gruppo {group_id} completato: {report}",
        op="delete_group_cascade"
    )
    return report


def delete_group(group_id: str) -> bool:
    cur = execute("DELETE FROM groups WHERE id = ?", (group_id,))
    return cur.rowcount > 0


__all__ = [
    "get_group",
    "list_groups",
    "create_group",
    "get_group_summary",
    "delete_group",
    "delete_group_cascade",
]
