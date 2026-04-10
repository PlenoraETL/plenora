"""
Database Service — facade di compatibilità per URBY-ETL.

Dopo la ristrutturazione in tre DB separati, questo modulo
mantiene la stessa API pubblica delegando ai nuovi service:

  - config_db_service  → SQLite #1 (config.db)
  - results_db_service → SQLite #2 (results.db)

Il codice esistente che importa da app.db.db_service continua
a funzionare senza modifiche.

Per nuovo codice preferire i service specifici:
  from app.db import config_db_service as cfg_db
  from app.db import results_db_service as res_db
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
    get_file_source, list_file_sources, save_file_source,
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
    """Inizializza SQLite #1 e #2. Chiamato da main.py all'avvio."""
    import app.db.config_db_service as cfg
    import app.db.results_db_service as res
    cfg.init_db()
    res.init_db()
    import app.core.intermediate.db as idb
    idb.init_db()
