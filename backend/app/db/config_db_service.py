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


# GROUPS

from app.db.config.groups import (  # noqa: F401,E402
    get_group,
    list_groups,
    create_group,
    get_group_summary,
    delete_group,
    delete_group_cascade,
)


# USERS

from app.db.config.users import (  # noqa: F401,E402
    get_user,
    get_user_by_username,
    list_users,
    create_user,
    update_user,
    update_user_password,
    delete_user,
)


# PROJECTS

from app.db.config.projects import (  # noqa: F401,E402
    get_project,
    list_projects,
    create_project,
    update_project,
    delete_project,
    get_project_retention_n,
    update_project_canvas_key_atomic,
)


# COMPARISON KEYS (per confronto L3 su intermediate.db)

from app.db.config.comparison_keys import delete_comparison_keys  # noqa: F401,E402


# CONNECTIONS

from app.db.config.connections import (  # noqa: F401,E402
    get_connection_config,
    list_connections,
    create_connection,
    update_connection,
    delete_connection,
)


# FILE SOURCES

from app.db.config.file_sources import (  # noqa: F401,E402
    FileSourceRow,
    get_file_source,
    list_file_sources,
    save_file_source,
    save_file_sources_batch,
    update_file_source_metadata,
    delete_file_source,
)


# DATASOURCES

from app.db.config.datasources import (  # noqa: F401,E402
    get_datasource,
    list_datasources,
    save_datasource,
    update_datasource,
    delete_datasource,
)


# API CONNECTIONS

from app.db.config.api_connections import (  # noqa: F401,E402
    get_api_connection,
    list_api_connections,
    save_api_connection,
    create_api_connection,
    update_api_connection,
    delete_api_connection,
)


# SCHEDULES

from app.db.config.schedules import (  # noqa: F401,E402
    get_schedule,
    list_schedules,
    save_schedule,
    update_schedule,
    delete_schedule,
)
