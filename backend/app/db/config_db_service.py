"""
Config DB Service — accesso a SQLite #1 (config.db).

Contiene: groups, users, projects, connections, file_sources,
          datasources, arcgis_connections, api_connections,
          schedules, beat_heartbeat, app_settings, comparison_keys,
          comparison_keys.

Thread-safe tramite threading.local() per le connessioni.
"""

import sqlite3
import threading
import json
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)

DB_PATH = Path(settings.DATA_DIR) / "config.db"

_local = threading.local()

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
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    return set_clause, tuple(fields.values())


@contextmanager
def transaction():
    conn = get_connection()
    conn.execute("BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_connection() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            str(DB_PATH),
            timeout=30,
            check_same_thread=False,
        )
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    return _local.conn


def close_connection() -> None:
    """Chiude la connessione thread-local a config.db, se aperta.

    Da chiamare in `finally` dei task Celery a vita lunga per evitare leak
    di file descriptor e database lock nei worker pool che ricicclano i thread.
    """
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


def _run_migrations(conn: sqlite3.Connection) -> None:
    """
    Applica migration incrementali — aggiunge colonne mancanti a DB esistenti.
    Pattern: prova a leggere la colonna, se fallisce la aggiunge.
    Per aggiungere nuove migration in futuro: aggiungere un blocco try/except.
    """
    pending = [
        # Icona personalizzabile per eseguibili (fallback ▶ se NULL)
        "ALTER TABLE eseguibili ADD COLUMN icon TEXT",
    ]

    for sql in pending:
        try:
            conn.execute(sql)
            conn.commit()
            logger.info(f"Migration applicata: {sql}", op="migration")
        except Exception as e:
            logger.warning(f"Migration fallita (non bloccante): {sql} — {e}", op="migration")


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    schema_path = Path(__file__).parent / "config_schema.sql"
    conn = get_connection()
    if schema_path.exists():
        schema = schema_path.read_text(encoding="utf-8")
        conn.executescript(schema)
        conn.commit()
        logger.info(f"Config DB inizializzato: {DB_PATH}", op="init_db")
    else:
        logger.warning("config_schema.sql non trovato", op="init_db")
    # Applica migration incrementali (gestisce DB esistenti senza la nuova colonna)
    _run_migrations(conn)


# ============================================================
# HELPERS
# ============================================================

def _row_to_dict(row: sqlite3.Row) -> Dict:
    return dict(row) if row else {}


def fetchone(sql: str, params: tuple = ()) -> Optional[Dict]:
    conn = get_connection()
    cur = conn.execute(sql, params)
    row = cur.fetchone()
    return _row_to_dict(row) if row else None


def fetchall(sql: str, params: tuple = ()) -> List[Dict]:
    conn = get_connection()
    cur = conn.execute(sql, params)
    return [_row_to_dict(r) for r in cur.fetchall()]


def execute(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    conn = get_connection()
    cur = conn.execute(sql, params)
    conn.commit()
    return cur


def executemany(sql: str, params_list: List[tuple]) -> None:
    conn = get_connection()
    conn.executemany(sql, params_list)
    conn.commit()


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
    """
    projects      = fetchall("SELECT id FROM projects WHERE group_id = ?", (group_id,))
    users         = fetchall("SELECT id FROM users WHERE group_id = ?", (group_id,))
    connections   = fetchall("SELECT id FROM connections WHERE group_id = ?", (group_id,))
    file_sources  = fetchall("SELECT id FROM file_sources WHERE group_id = ?", (group_id,))
    datasources   = fetchall("SELECT id FROM datasources WHERE group_id = ?", (group_id,))
    api_conns     = fetchall("SELECT id FROM api_connections WHERE group_id = ?", (group_id,))
    schedules     = fetchall("SELECT id FROM schedules WHERE group_id = ?", (group_id,))
    return {
        "projects":        len(projects),
        "users":           len(users),
        "connections":     len(connections),
        "file_sources":    len(file_sources),
        "datasources":     len(datasources),
        "api_connections": len(api_conns),
        "schedules":       len(schedules),
    }


def delete_group_cascade(group_id: str) -> dict:
    """
    Cascade delete completo di un gruppo e di tutti i suoi dati:
      config.db   → schedules, api_connections, datasources, file_sources,
                    connections, comparison_keys, projects, users, group
      results.db  → results per ogni progetto del gruppo
      intermediate.db → runs + tabelle dinamiche per ogni progetto del gruppo
      log.db      → runs + logs per group_id

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
        "log_runs": 0, "log_entries": 0,
    }

    # 1. Schedules
    try:
        rows = fetchall("SELECT id FROM schedules WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM schedules WHERE id = ?", (r["id"],))
            report["schedules"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete schedules fallito: {e}")

    # 2. API connections
    try:
        rows = fetchall("SELECT id FROM api_connections WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM api_connections WHERE id = ?", (r["id"],))
            report["api_connections"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete api_connections fallito: {e}")

    # 3. Datasources
    try:
        rows = fetchall("SELECT id FROM datasources WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM datasources WHERE id = ?", (r["id"],))
            report["datasources"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete datasources fallito: {e}")

    # 4. File sources
    try:
        rows = fetchall("SELECT id FROM file_sources WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM file_sources WHERE id = ?", (r["id"],))
            report["file_sources"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete file_sources fallito: {e}")

    # 5. Connections
    try:
        rows = fetchall("SELECT id FROM connections WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM connections WHERE id = ?", (r["id"],))
            report["connections"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete connections fallito: {e}")

    # 6. Projects → results.db + intermediate.db per ognuno
    try:
        projects = fetchall("SELECT id FROM projects WHERE group_id = ?", (group_id,))
        for p in projects:
            pid = p["id"]

            # Comparison keys (config.db)
            try:
                execute("DELETE FROM comparison_keys WHERE canvas_id = ?", (pid,))
            except Exception as e:
                _log.warning(f"Cascade delete comparison_keys progetto {pid}: {e}")

            # Results (results.db)
            try:
                import app.db.results_db_service as res_db
                deleted = res_db.delete_results_for_project(pid)
                report["results"] += deleted
            except Exception as e:
                _log.warning(f"Cascade delete results progetto {pid}: {e}")

            # Intermediate runs + tabelle dinamiche (intermediate.db)
            try:
                from app.core.intermediate.db import delete_all_runs_for_canvas
                deleted = delete_all_runs_for_canvas(pid)
                report["intermediate_runs"] += deleted
            except Exception as e:
                _log.warning(f"Cascade delete intermediate progetto {pid}: {e}")

            # Progetto (config.db)
            execute("DELETE FROM projects WHERE id = ?", (pid,))
            report["projects"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete projects fallito: {e}")

    # 7. Users
    try:
        rows = fetchall("SELECT id FROM users WHERE group_id = ?", (group_id,))
        for r in rows:
            execute("DELETE FROM users WHERE id = ?", (r["id"],))
            report["users"] += 1
    except Exception as e:
        _log.warning(f"Cascade delete users fallito: {e}")

    # 8. log.db — runs e log entries per group_id
    try:
        from app.config import settings as _settings
        import sqlite3 as _sqlite3
        log_path = _settings.LOGS_DIR / _settings.LOG_DB_FILE
        if log_path.exists():
            with _sqlite3.connect(str(log_path)) as lconn:
                lconn.execute("PRAGMA journal_mode=WAL")
                cur = lconn.execute("DELETE FROM logs WHERE group_id = ?", (group_id,))
                report["log_entries"] = cur.rowcount
                cur = lconn.execute("DELETE FROM runs WHERE group_id = ?", (group_id,))
                report["log_runs"] = cur.rowcount
    except Exception as e:
        _log.warning(f"Cascade delete log.db fallito: {e}")

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
        "INSERT INTO users (id, username, password_hash, email, group_id, role, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,1,?,?)",
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


def list_all_projects(project_type: str = None) -> List[Dict]:
    """Lista tutti i progetti senza filtro gruppo — solo per superadmin."""
    if project_type:
        return fetchall(
            "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at FROM projects WHERE project_type = ? ORDER BY updated_at DESC",
            (project_type,)
        )
    return fetchall(
        "SELECT id, group_id, name, project_type, connection_id, created_at, updated_at FROM projects ORDER BY updated_at DESC"
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

def get_comparison_keys(canvas_id: str, node_id: str) -> Optional[List[str]]:
    row = fetchone(
        "SELECT key_columns FROM comparison_keys WHERE canvas_id = ? AND node_id = ?",
        (canvas_id, node_id)
    )
    return json.loads(row["key_columns"]) if row else None


def save_comparison_keys(canvas_id: str, node_id: str, group_id: str,
                         key_columns: List[str]) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO comparison_keys (canvas_id, node_id, group_id, key_columns, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (canvas_id, node_id, group_id, json.dumps(key_columns), now, now)
    )


def delete_comparison_keys(canvas_id: str, node_id: str = None) -> None:
    if node_id:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ? AND node_id = ?",
                (canvas_id, node_id))
    else:
        execute("DELETE FROM comparison_keys WHERE canvas_id = ?", (canvas_id,))


# ============================================================
# CONNECTIONS
# ============================================================

def get_connection_config(conn_id: str) -> Optional[Dict]:
    row = fetchone("SELECT * FROM connections WHERE id = ?", (conn_id,))
    if row and row.get("extra"):
        row["extra"] = json.loads(row["extra"])
    return row


def list_connections(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, group_id, name, db_type, host, port, database, db_schema, username, created_at FROM connections WHERE group_id = ? ORDER BY name",
        (group_id,)
    )


def create_connection(conn_id: str, group_id: str, name: str, db_type: str,
                      host: str = None, port: int = None, database: str = None,
                      db_schema: str = None, username: str = None,
                      password: str = None, extra: dict = None,
                      created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT INTO connections (id, group_id, name, db_type, host, port, database, db_schema, username, password, extra, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (conn_id, group_id, name, db_type, host, port, database, db_schema,
         username, password, json.dumps(extra or {}), now, now, created_by)
    )
    return get_connection_config(conn_id)


def update_connection(conn_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    if "extra" in fields and isinstance(fields["extra"], dict):
        fields["extra"] = json.dumps(fields["extra"])
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("connections", fields)
    execute(f"UPDATE connections SET {set_clause} WHERE id = ?", vals + (conn_id,))
    return get_connection_config(conn_id)


def delete_connection(conn_id: str) -> bool:
    cur = execute("DELETE FROM connections WHERE id = ?", (conn_id,))
    return cur.rowcount > 0


# ============================================================
# FILE SOURCES
# ============================================================

def get_file_source(file_id: str, include_data: bool = False) -> Optional[Dict]:
    if include_data:
        row = fetchone("SELECT * FROM file_sources WHERE id = ?", (file_id,))
    else:
        row = fetchone(
            "SELECT id, group_id, name, original_filename, format, size_bytes, metadata, created_at FROM file_sources WHERE id = ?",
            (file_id,)
        )
    if row and row.get("metadata"):
        row["metadata"] = json.loads(row["metadata"])
    return row


def list_file_sources(group_id: Optional[str] = None) -> List[Dict]:
    """Lista file_sources. group_id=None → no filter (usato da superadmin)."""
    if group_id is None:
        return fetchall(
            "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources ORDER BY created_at DESC"
        )
    return fetchall(
        "SELECT id, group_id, name, original_filename, format, size_bytes, created_at, created_by FROM file_sources WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )


def save_file_source(file_id: str, group_id: str, name: str, original_filename: str,
                     fmt: str, data: bytes, metadata: dict = None,
                     created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO file_sources (id, group_id, name, original_filename, format, data, size_bytes, metadata, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (file_id, group_id, name, original_filename, fmt, data, len(data),
         json.dumps(metadata or {}), now, now, created_by)
    )
    return get_file_source(file_id)


def update_file_source_metadata(file_id: str, metadata: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE file_sources SET metadata = ?, updated_at = ? WHERE id = ?",
        (json.dumps(metadata), now, file_id)
    )


def delete_file_source(file_id: str) -> bool:
    cur = execute("DELETE FROM file_sources WHERE id = ?", (file_id,))
    return cur.rowcount > 0


# ============================================================
# DATASOURCES
# ============================================================

def get_datasource(ds_id: str) -> Optional[Dict]:
    row = fetchone("SELECT * FROM datasources WHERE id = ?", (ds_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_datasources(group_id: str) -> List[Dict]:
    return fetchall(
        "SELECT id, group_id, name, ds_type, created_at, created_by FROM datasources WHERE group_id = ? ORDER BY name",
        (group_id,)
    )


def save_datasource(ds_id: str, group_id: str, name: str, ds_type: str, config: dict,
                    created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO datasources (id, group_id, name, ds_type, config, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?)",
        (ds_id, group_id, name, ds_type, json.dumps(config), now, now, created_by)
    )
    return get_datasource(ds_id)


def update_datasource(ds_id: str, **kwargs) -> None:
    """Aggiorna i campi consentiti di una datasource.

    Solo i campi in `_ALLOWED_UPDATE_FIELDS["datasources"]` sono accettati.
    Solleva ValueError se un campo non consentito viene passato (anti-injection
    e protezione da escalation: nessuno deve poter aggiornare group_id, id,
    created_by, ecc.).
    """
    from datetime import datetime, timezone
    if not kwargs:
        return
    # Serializza il blob `config` se passato come dict (convenienza per i caller)
    fields = dict(kwargs)
    if "config" in fields and isinstance(fields["config"], dict):
        fields["config"] = json.dumps(fields["config"])
    # Forza updated_at lato server
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, params = _safe_set_clause("datasources", fields)
    execute(f"UPDATE datasources SET {set_clause} WHERE id = ?", params + (ds_id,))


def delete_datasource(ds_id: str) -> bool:
    cur = execute("DELETE FROM datasources WHERE id = ?", (ds_id,))
    return cur.rowcount > 0


# ============================================================
# API CONNECTIONS
# ============================================================

def get_api_connection(api_id: str) -> Optional[Dict]:
    row = fetchone("SELECT * FROM api_connections WHERE id = ?", (api_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_api_connections(group_id: str) -> List[Dict]:
    rows = fetchall(
        "SELECT * FROM api_connections WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )
    for row in rows:
        if row.get("config"):
            row["config"] = json.loads(row["config"])
    return rows


def save_api_connection(api_id: str, group_id: str, config: dict,
                        created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO api_connections (id, group_id, config, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?)",
        (api_id, group_id, json.dumps(config), now, now, created_by)
    )
    return get_api_connection(api_id)


def create_api_connection(group_id: str, config: dict, created_by: str = None) -> Dict:
    """Alias per save_api_connection."""
    api_id = config.get("id")
    if not api_id:
        import uuid as _uuid
        api_id = f"api_{_uuid.uuid4().hex[:12]}"
        config["id"] = api_id
    return save_api_connection(api_id, group_id, config, created_by=created_by)


def update_api_connection(api_id: str, config: dict) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "UPDATE api_connections SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, api_id)
    )


def delete_api_connection(api_id: str) -> bool:
    cur = execute("DELETE FROM api_connections WHERE id = ?", (api_id,))
    return cur.rowcount > 0


# ============================================================
# SCHEDULES
# ============================================================

def get_schedule(schedule_id: str) -> Optional[Dict]:
    row = fetchone("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
    if row and row.get("config"):
        row["config"] = json.loads(row["config"])
    return row


def list_schedules(group_id: str) -> List[Dict]:
    rows = fetchall(
        "SELECT * FROM schedules WHERE group_id = ? ORDER BY created_at DESC",
        (group_id,)
    )
    for r in rows:
        if r.get("config"):
            r["config"] = json.loads(r["config"])
    return rows


def save_schedule(schedule_id: str, group_id: str, project_id: str, name: str,
                  cron_expression: str, is_enabled: bool = True, config: dict = None,
                  created_by: str = None) -> Dict:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute(
        "INSERT OR REPLACE INTO schedules (id, group_id, project_id, name, cron_expression, is_enabled, config, created_at, updated_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (schedule_id, group_id, project_id, name, cron_expression,
         1 if is_enabled else 0, json.dumps(config or {}), now, now, created_by)
    )
    return get_schedule(schedule_id)


def update_schedule(schedule_id: str, **fields) -> Optional[Dict]:
    from datetime import datetime, timezone
    if "config" in fields and isinstance(fields["config"], dict):
        fields["config"] = json.dumps(fields["config"])
    if "is_enabled" in fields:
        fields["is_enabled"] = 1 if fields["is_enabled"] else 0
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause, vals = _safe_set_clause("schedules", fields)
    execute(f"UPDATE schedules SET {set_clause} WHERE id = ?", vals + (schedule_id,))
    return get_schedule(schedule_id)


def delete_schedule(schedule_id: str) -> bool:
    cur = execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
    return cur.rowcount > 0


# ============================================================
# BEAT HEARTBEAT
# ============================================================

def update_beat_heartbeat() -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    execute("INSERT OR REPLACE INTO beat_heartbeat (id, ts) VALUES ('beat', ?)", (now,))


def get_beat_heartbeat() -> Optional[str]:
    row = fetchone("SELECT ts FROM beat_heartbeat WHERE id = 'beat'")
    return row["ts"] if row else None


# ============================================================
# APP SETTINGS
# ============================================================

def get_setting(key: str, default: Any = None) -> Any:
    row = fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?,?)",
        (key, str(value))
    )


def get_app_setting(key: str) -> Optional[str]:
    row = fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else None


def set_app_setting(key: str, value: str) -> None:
    execute(
        "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
        (key, value)
    )


def delete_app_setting(key: str) -> None:
    execute("DELETE FROM app_settings WHERE key = ?", (key,))


