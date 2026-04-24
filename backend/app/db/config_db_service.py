"""
Config DB Service — accesso a PostgreSQL (ex SQLite config.db).

Contiene: groups, users, projects, connections, file_sources,
          datasources, arcgis_connections, api_connections,
          schedules, beat_heartbeat, app_settings, comparison_keys.

Migrato da SQLite a PostgreSQL: usa pg_connection pool.
Le funzioni base (fetchone, fetchall, execute, transaction) sono
delegate a pg_connection. Le funzioni CRUD restano qui.
"""

import json
from typing import Any, Dict, List, Optional
from app.utils.logger import get_logger

# Importa tutto da pg_connection (rimpiazza sqlite3 thread-local)
from app.db.pg_connection import (
    fetchone, fetchall, execute, executemany,
    transaction, get_connection, put_connection,
    close_connection, init_db as _pg_init_db,
)

logger = get_logger(__name__)

# Whitelist campi aggiornabili per tabella
_ALLOWED_UPDATE_FIELDS = {
    "users":       {"username", "email", "role", "is_active", "password_hash", "updated_at"},
    "projects":    {"name", "description", "connection_id", "canvas", "columns_metadata",
                    "updated_at", "retention_n"},
    "connections": {"name", "db_type", "host", "port", "database", "username", "password",
                    "db_schema", "extra", "is_active", "updated_at"},
    "schedules":   {"name", "description", "config", "is_enabled", "cron_expr",
                    "last_run_at", "last_run_status", "last_run_rows", "next_run_at",
                    "run_count", "error_count", "updated_at",
                    # nomi usati da save_schedule
                    "cron_expression", "last_run", "last_status", "next_run"},
    # Schema reale: id, group_id, name, ds_type, config, created_at, updated_at, created_by
    # In pratica il service aggiorna solo `config` (blob JSON), ma teniamo
    # name/ds_type per future operazioni di rename.
    "datasources": {"name", "ds_type", "config", "updated_at"},
}


def _safe_set_clause(table: str, fields: dict) -> tuple:
    allowed = _ALLOWED_UPDATE_FIELDS.get(table, set())
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE {table}: {invalid}. "
            f"Consentiti: {allowed}"
        )
    # PostgreSQL usa %s come placeholder (pg_connection._adapt_params converte ? → %s,
    # ma qui costruiamo direttamente con %s per chiarezza)
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    return set_clause, tuple(fields.values())


def init_db():
    """Verifica connessione PostgreSQL. Le tabelle sono già su PG."""
    _pg_init_db()


# ============================================================
# GROUPS
# ============================================================

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


# ============================================================
# USERS
# ============================================================

def get_user(user_id: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_username(username: str) -> Optional[Dict]:
    return fetchone("SELECT * FROM users WHERE username = ?", (username,))


def list_users(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, username, email, group_id, role, is_active, created_at FROM users WHERE group_id = ? ORDER BY username",
        (group_id,)
    )


def create_user(user_id: str, username: str, password_hash: str, group_id: str,
                role: str = "user", email: str = "") -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO users (id, username, password_hash, email, group_id, role, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,TRUE,?,?)",
        (user_id, username, password_hash, email, group_id, role, now, now)
    )
    return get_user(user_id)


def update_user(user_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("users", fields)
    execute(f"UPDATE users SET {set_clause} WHERE id = ?", vals + (user_id,))
    return get_user(user_id)


def update_user_password(user_id: str, password_hash: str) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
            (password_hash, now, user_id))


def delete_user(user_id: str) -> bool:
    cur = execute("DELETE FROM users WHERE id = ?", (user_id,))
    return cur.rowcount > 0


# ============================================================
# PROJECTS
# ============================================================

def get_project(project_id: str, group_id: str = None) -> Optional[Dict]:
    if group_id:
        row = fetchone(
            "SELECT * FROM projects WHERE id = ? AND group_id = ?",
            (project_id, group_id)
        )
    else:
        row = fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    if row and row.get("canvas"):
        # Su canvas corrotto: log warning + fallback a dict vuoto. Il caller
        # (ProjectService.get_project_canvas) ha la sua difesa aggiuntiva.
        try:
            row["canvas"] = json.loads(row["canvas"])
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning(
                f"Canvas corrotto in DB per progetto {project_id}: {e}. "
                f"Fallback a canvas vuoto."
            )
            row["canvas"] = {"nodes": [], "edges": []}
    return row


def list_projects(group_id: Optional[str] = None, project_type: str = None) -> List[Dict]:
    """Lista progetti. group_id=None → no filter (usato dal superadmin)."""
    if group_id is None:
        if project_type:
            return fetchall(
                "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE project_type = ? ORDER BY updated_at DESC",
                (project_type,)
            )
        return fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects ORDER BY updated_at DESC"
        )
    if project_type:
        return fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? AND project_type = ? ORDER BY updated_at DESC",
            (group_id, project_type)
        )
    return fetchall(
        "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at, created_by FROM projects WHERE group_id = ? ORDER BY updated_at DESC",
        (group_id,)
    )


def create_project(project_id: str, group_id: str, name: str, project_type: str,
                   connection_id: str = None, created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    canvas = json.dumps({"nodes": [], "edges": []})
    execute(
        "INSERT INTO projects (id, group_id, name, project_type, canvas, connection_id, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?)",
        (project_id, group_id, name, project_type, canvas, connection_id, now, now, created_by)
    )
    return get_project(project_id)


def update_project(project_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    if "canvas" in fields and isinstance(fields["canvas"], dict):
        fields["canvas"] = json.dumps(fields["canvas"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("projects", fields)
    execute(f"UPDATE projects SET {set_clause} WHERE id = ?", vals + (project_id,))
    return get_project(project_id)


def update_project_canvas_key_atomic(project_id: str, key: str, value) -> bool:
    """Aggiorna una singola chiave del canvas JSON in maniera atomica.

    Pattern: BEGIN; SELECT ... FOR UPDATE; merge; UPDATE; COMMIT.
    Il SELECT FOR UPDATE blocca il row dal momento del check fino al
    COMMIT, serializzando scritture concorrenti sullo stesso progetto
    (cross-process: worker Celery + uvicorn handler + altri worker).

    Senza questo, due write concorrenti facevano lost-update:
      - A: canvas = get(); canvas["columns_metadata"] = X; update()
      - B: canvas = get(); canvas["nodes"] = Y;            update()
    Se B lo pone dopo A ma legge prima, X va perso. Ora il 2°
    SELECT FOR UPDATE aspetta il 1° COMMIT → vede il canvas gia'
    aggiornato e fa merge sopra.

    Nota: `canvas` in DB e' TEXT (JSON serializzato), non jsonb → non
    possiamo usare `jsonb_set` nativo. Il lock pessimistico e' la
    strategia piu' semplice che funziona sulla colonna attuale senza
    migration di schema.

    Ritorna True se aggiornato, False se progetto inesistente.
    Solleva sulle altre eccezioni (rollback automatico via transaction).
    """
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with transaction() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT canvas FROM projects WHERE id = %s FOR UPDATE",
                (project_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            canvas_raw = row["canvas"] if isinstance(row, dict) else row[0]
            if isinstance(canvas_raw, str):
                try:
                    canvas_obj = json.loads(canvas_raw) if canvas_raw else {}
                except json.JSONDecodeError:
                    # Canvas corrotto: non vogliamo sovrascriverlo silent
                    # (come documentato in project_service.get_project_canvas).
                    # Solleva → rollback automatico del lock.
                    raise ValueError(
                        f"Canvas corrotto per progetto {project_id}: JSON non parsabile. "
                        f"Rifiuto di sovrascrivere — ripristinare da backup."
                    )
            elif canvas_raw is None:
                canvas_obj = {}
            elif isinstance(canvas_raw, dict):
                canvas_obj = canvas_raw
            else:
                raise ValueError(
                    f"Canvas di tipo inatteso per progetto {project_id}: "
                    f"{type(canvas_raw).__name__}"
                )

            canvas_obj[key] = value
            cur.execute(
                "UPDATE projects SET canvas = %s, updated_at = %s WHERE id = %s",
                (json.dumps(canvas_obj), now, project_id),
            )
    return True


def delete_project(project_id: str, group_id: str = None) -> bool:
    if group_id:
        cur = execute("DELETE FROM projects WHERE id = ? AND group_id = ?", (project_id, group_id))
    else:
        cur = execute("DELETE FROM projects WHERE id = ?", (project_id,))
    return cur.rowcount > 0


def get_project_retention_n(project_id: str) -> Optional[int]:
    """Ritorna retention_n del canvas (None = usa default globale)."""
    row = fetchone("SELECT retention_n FROM projects WHERE id = ?", (project_id,))
    return row["retention_n"] if row else None


# ============================================================
# COMPARISON KEYS (per confronto L3 su intermediate.db)
# ============================================================

# Re-export: estratto in `app/db/config/comparison_keys.py` — scope micro Batch G #9,
# invariants §6 commit `58d98a0`. Facade preservata per zero call site touched.
from app.db.config.comparison_keys import delete_comparison_keys  # noqa: F401,E402


# ============================================================
# CONNECTIONS
# ============================================================

# Re-export: estratto in `app/db/config/connections.py` — scope micro Batch G #9
# quinta riapertura, invariants §6 commit `2dee7da`. Facade preservata per zero
# call site touched (consumer reale: services/connection_service.py 15+ usi via
# `import app.db.db_service as db` + db_service.py:23-27 re-export).
from app.db.config.connections import (  # noqa: F401,E402
    get_connection_config,
    list_connections,
    create_connection,
    update_connection,
    delete_connection,
)


# ============================================================
# FILE SOURCES
# ============================================================

# Re-export: estratto in `app/db/config/file_sources.py` — scope micro Batch G #9
# quarta riapertura, invariants §6 commit `093d22b`. Facade preservata per zero
# call site touched (consumer: db_service.py re-export + file_service.py 27 usi +
# core/files/router.py + core/tickets/router.py + core/arricchisci/api_connector.py
# [attributo via facade, no edit] + shared/authz_helpers.py + core/auth/auth.py +
# workers/tasks/enrichment_task.py + 10 test file patch target).
from app.db.config.file_sources import (  # noqa: F401,E402
    get_file_source,
    list_file_sources,
    save_file_source,
    update_file_source_metadata,
    delete_file_source,
)


# ============================================================
# DATASOURCES
# ============================================================

# Re-export: estratto in `app/db/config/datasources.py` — scope micro Batch G #9
# terza riapertura, invariants §6 commit `86ea1c5`. Facade preservata per zero
# call site touched (consumer: db_service.py:23-24 re-export + core/datasources/
# service.py 4 import locali + 4 test file patch target).
from app.db.config.datasources import (  # noqa: F401,E402
    get_datasource,
    list_datasources,
    save_datasource,
    update_datasource,
    delete_datasource,
)


# ============================================================
# API CONNECTIONS
# ============================================================

# Re-export: estratto in `app/db/config/api_connections.py` — scope micro Batch G
# #9 sesta riapertura (hard-escalation governance), invariants §6 commit
# `b8b3396`. Facade preservata per zero call site touched (consumer DAL
# principale: api_connector.py in freeze god-class §6 #9 via `db_svc.` alias
# L73, 7 chiamate DAL L193/269/324/343/381/388/390 invariate; api_connector.py
# file NON toccato — freeze vincola massa netta, non dipendenze DAL esterne).
from app.db.config.api_connections import (  # noqa: F401,E402
    get_api_connection,
    list_api_connections,
    save_api_connection,
    create_api_connection,
    update_api_connection,
    delete_api_connection,
)


# ============================================================
# SCHEDULES
# ============================================================

# Re-export: estratto in `app/db/config/schedules.py` — scope micro Batch G #9
# seconda riapertura, invariants §6 commit `61b5058`. Facade preservata per zero
# call site touched (consumer: db_service.py:27 re-export + core/scheduler/* +
# core/auth/auth.py + 7 test patch target).
from app.db.config.schedules import (  # noqa: F401,E402
    get_schedule,
    list_schedules,
    save_schedule,
    update_schedule,
    delete_schedule,
)


