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

# Re-export: 6 funzioni estratte in `app/db/config/groups.py` —
# BE-GO-READY-DB-ENGINE-002-GROUPS-CLUSTER-EXTRACTION. Facade
# preservata per zero-call-site-touched: i monkeypatch esistenti
# (`monkeypatch.setattr(cfg_db, "get_group", ...)`,
# `monkeypatch.setattr(cfg_db, "delete_group_cascade", ...)`)
# continuano a funzionare perche' i nomi sono attributi modulo
# che puntano alle funzioni del leaf. Mirror del pattern gia'
# applicato a users/projects/connections/file_sources/datasources/
# api_connections/schedules.
#
# BE-GO-READY-DB-ENGINE-003-GROUPS-CALLSITE-MIGRATION: due caller
# di production migrati al leaf canonico:
#   - `app/core/auth/groups_router.py` -> `from app.db.config import
#     groups as _groups` (rimosso dall'allowlist strangler);
#   - `app/services/project_service.py::_ensure_default_group` -> 2
#     callsite migrati al leaf; il file resta in allowlist solo per
#     `update_project_canvas_key_atomic` (kept in the facade).
# Caller residui via `db.*` re-export:
#   - `app/db/db_service.py` (cross-engine bulk surface, KEEP_COMPAT).
from app.db.config.groups import (  # noqa: F401,E402
    get_group,
    list_groups,
    create_group,
    get_group_summary,
    delete_group,
    delete_group_cascade,
)


# ============================================================
# USERS
# ============================================================

# Re-export: estratto in `app/db/config/users.py` — scope micro Batch G #9
# settima riapertura (governance autonoma auth-critical), invariants §6 commit
# `16bebfa`. Facade preservata per zero call site touched (consumer:
# db_service.py:16-17 re-export + core/auth/auth.py 11 usi via `db.*` +
# shared/async_user_recheck.py:22 import diretto + 6 file test con patch target
# `auth_mod.db.*` e 1 patch diretto `app.db.config_db_service.get_user`).
# `_safe_set_clause` e `_ALLOWED_UPDATE_FIELDS["users"]` restano qui nel facade
# (cross-cluster shared).
from app.db.config.users import (  # noqa: F401,E402
    get_user,
    get_user_by_username,
    list_users,
    create_user,
    update_user,
    update_user_password,
    delete_user,
)


# ============================================================
# PROJECTS
# ============================================================

# Re-export: 6 funzioni pure DAL estratte in `app/db/config/projects.py` —
# scope micro Batch G #9 ottava riapertura (governance autonoma scope-split
# transazionale), invariants §6 commit `7a00c1f`. Facade preservata per zero
# call site touched (consumer: db_service.py:18 re-export 5 + 20+ file tra
# services/workers/utils + 14+ file test).
#
# BE-GO-READY-DB-ENGINE-007 (PROJECT-SERVICE-FACADE-CUT):
# `update_project_canvas_key_atomic` is now hosted in the projects
# leaf alongside the other 6 cluster functions. The re-export below
# keeps the facade attribute (`cfg_db.update_project_canvas_key_atomic`)
# available for tests / integrations that bind to the facade module
# (8 dedicated test files patch via `cfg_db.<name>`); production
# callers (project_service) now import from
# `app.db.config.projects` directly.
#
# `_safe_set_clause` e `_ALLOWED_UPDATE_FIELDS["projects"]` restano nel facade
# (cross-cluster shared — lazy import in `config/projects.update_project`).
from app.db.config.projects import (  # noqa: F401,E402
    get_project,
    list_projects,
    create_project,
    update_project,
    delete_project,
    get_project_retention_n,
    update_project_canvas_key_atomic,
)


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
    FileSourceRow,
    get_file_source,
    list_file_sources,
    save_file_source,
    save_file_sources_batch,
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


