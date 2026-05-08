"""
Config DB Service — accesso a PostgreSQL (ex SQLite config.db).

Contiene: groups, users, projects, connections, file_sources,
"""

# KEEP_COMPAT_WITH_REMOVAL_CONDITION: remove after functional and integration
# harnesses import the canonical domain DB modules directly.
# Owner: DB_BOUNDARY_ENGINE

from app.utils.logger import get_logger

# Importa tutto da pg_connection (rimpiazza sqlite3 thread-local
from app.db.pg_connection import (
    fetchone, fetchall, execute, executemany,
    transaction, get_connection, put_connection,
    close_connection, init_db as _pg_init_db,
)

logger = get_logger(__name__)


def init_db():
    """Verifica connessione PostgreSQL. Le tabelle sono già su PG."""
    _pg_init_db()


# GROUPS

from app.core.auth.db import (  # noqa: F401,E402
    get_group,
    list_groups,
    create_group,
    get_group_summary,
    delete_group,
)
from app.core.auth.group_cleanup import delete_group_cascade  # noqa: F401,E402


# USERS

from app.core.auth.db import (  # noqa: F401,E402
    get_user,
    get_user_by_username,
    list_users,
    create_user,
    update_user,
    update_user_password,
    delete_user,
)


# PROJECTS

from app.core.projects.db import (  # noqa: F401,E402
    list_projects,
    delete_project,
    get_project_retention_n,
    get_project_row as _get_project_row,
    create_project_row as _create_project_row,
    update_project_row as _update_project_row,
    update_project_canvas_key_atomic,
)


def _project_detail_compat_payload(row):
    return {
        "id": row.id,
        "group_id": row.group_id,
        "name": row.name,
        "project_type": row.project_type,
        "canvas": row.canvas,
        "connection_id": row.connection_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "created_by": row.created_by,
        "description": row.description,
        "retention_n": row.retention_n,
    }


def get_project(project_id, group_id=None):
    """Legacy config facade wrapper around typed project-domain reads."""
    row = _get_project_row(project_id, group_id=group_id)
    return _project_detail_compat_payload(row) if row else None


def create_project(
    project_id,
    group_id,
    name,
    project_type,
    connection_id=None,
    created_by=None,
):
    """Legacy config facade wrapper around typed project-domain creation."""
    row = _create_project_row(
        project_id=project_id,
        group_id=group_id,
        name=name,
        project_type=project_type,
        connection_id=connection_id,
        created_by=created_by,
    )
    return _project_detail_compat_payload(row)


def update_project(project_id, **fields):
    """Legacy config facade wrapper around typed project-domain updates."""
    row = _update_project_row(project_id, **fields)
    return _project_detail_compat_payload(row) if row else None


# COMPARISON KEYS (per confronto L3 su intermediate.db

from app.core.projects.db import delete_comparison_keys  # noqa: F401,E402


# CONNECTIONS

from app.core.connections.db import (  # noqa: F401,E402
    get_connection_config,
    list_connection_configs as list_connections,
    create_connection,
    update_connection,
    delete_connection,
)


# FILE SOURCES

from app.core.files.db import (  # noqa: F401,E402
    FileSourceRow,
    get_file_source,
    list_file_sources,
    save_file_source,
    save_file_sources_batch,
    update_file_source_metadata,
    delete_file_source,
)


# DATASOURCES - explicit domain-owned persistence surface

from app.core.datasources.db import (  # noqa: F401,E402
    DataSourceRow,
    get_datasource,
    list_datasources,
    save_datasource,
    update_datasource,
    delete_datasource,
)


# API CONNECTIONS

from app.core.arricchisci.db import (  # noqa: F401,E402
    get_api_connection,
    list_api_connections,
    save_api_connection,
    create_api_connection,
    update_api_connection,
    delete_api_connection,
)


# SCHEDULES

from app.core.scheduler.db import (  # noqa: F401,E402
    get_schedule,
    list_schedules,
    save_schedule,
    update_schedule,
    delete_schedule,
)
