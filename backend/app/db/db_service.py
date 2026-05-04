"""
Database Service — facade di compatibilità per PLENORA-ETL.

Migrato da 3 SQLite separati a un singolo PostgreSQL.
Questo modulo mantiene la stessa API pubblica delegando ai service
specifici che ora usano tutti pg_connection come backend.

Il codice esistente che importa da app.db.db_service continua
a funzionare senza modifiche.
"""

# Re-export completo da config_db_service (SQLite #1)
from app.db.config_db_service import (
    get_connection, init_db, transaction, fetchone, fetchall, execute, executemany,
    get_group, list_groups, create_group, delete_group,
    get_user, get_user_by_username, list_users, create_user,
    update_user, update_user_password, delete_user,
    get_project, list_projects, create_project, update_project, delete_project,
    get_connection_config, list_connections, create_connection,
    update_connection, delete_connection,
    FileSourceRow,
    get_file_source, list_file_sources, save_file_source,
    save_file_sources_batch,
    update_file_source_metadata, delete_file_source,
    get_datasource, list_datasources, save_datasource,
    update_datasource, delete_datasource,
    get_api_connection, list_api_connections, save_api_connection,
    update_api_connection, delete_api_connection,
    get_schedule, list_schedules, save_schedule, update_schedule, delete_schedule,
)

# Re-export da results_db_service (SQLite #2)
from app.db.results_db_service import (
    save_result, get_result, get_result_data, get_latest_result,
    list_results, delete_result,
    log_execution, update_log, list_logs,
)


def init_all_dbs():
    """Inizializza PostgreSQL. Chiamato da main.py all'avvio."""
    from app.db.pg_connection import init_db as pg_init
    pg_init()
    # intermediate.db: anche questo va su PostgreSQL
    import app.core.intermediate.db as idb
    idb.init_db()
    # C8-nit: runner migration — applica i file in migrations/ non ancora
    # registrati in <DB_SCHEMA>.schema_migrations. I file esistenti usano
    # IF NOT EXISTS (idempotenti) quindi il primo run post-C8 li registra
    # semplicemente come applicate senza side-effect.
    from app.db.migrations_runner import apply_pending_migrations
    apply_pending_migrations()
