"""Allowlist for the production dead-code guard.

Each pattern documents a class of Vulture false positives that is intentionally
kept alive by framework registration, dynamic dispatch, protocols, or explicit
compatibility contracts.
"""

from __future__ import annotations

import re


ALLOWED_VULTURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    # FastAPI endpoint functions and app-level exception/health handlers.
    re.compile(r"backend[\\/]+app[\\/]+main\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+app_factory\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+auth[\\/]+auth\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+.+[\\/]router\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+.+[\\/].*_router\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+features[\\/]routes\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+.+[\\/]api[\\/]+.+\.py:\d+: unused function "),

    # Starlette/FastAPI middleware hooks are invoked by the framework.
    re.compile(r"backend[\\/]+app[\\/]+.+middleware\.py:\d+: unused method 'dispatch' "),

    # Celery tasks and signal callbacks are registered by decorators.
    re.compile(r"backend[\\/]+workers[\\/]+celery_app\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+workers[\\/]+tasks[\\/]+.+\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+workers[\\/]+beat_scheduler\.py:\d+: unused method "),

    # Transform functions are loaded from transforms_registry.json or plugin dispatch.
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+manipola[\\/]+transforms[\\/]+.+\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+manipola[\\/]+plugin_executor\.py:\d+: unused (method|function) "),

    # SQL translators resolve implementation methods by naming convention.
    re.compile(r"backend[\\/]+app[\\/]+db[\\/]+sql_.+\.py:\d+: unused method '_translate_"),
    re.compile(r"backend[\\/]+app[\\/]+db[\\/]+sql_translator\.py:\d+: unused (method|function) "),

    # Protocol/dataclass compatibility methods are public contracts.
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+files[\\/]+service_deps\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]+execution[\\/]+executor_protocol\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]+connections[\\/]+arcgis_provider\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+tickets[\\/]service\.py:\d+: unused (method|function) "),

    # Explicit test-support surfaces covered by contract tests.
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+results[\\/]result_reader\.py:\d+: unused function 'get_result_(as_bytes|as_tempfile|info)' "),
    re.compile(r"backend[\\/]+app[\\/]+(observability[\\/]audit_action|infrastructure[\\/]security[\\/]crypto|core[\\/]security[\\/]+(login_rate_limiter|token_blacklist))\.py:\d+: unused function '_.+_for_tests' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+arricchisci[\\/]_rate_limiter\.py:\d+: unused function '_.+_for_tests' "),

    # Public helper surfaces with direct unit/contract coverage.
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+arricchisci[\\/]api_connection_rest_file\.py:\d+: unused method '_assert_file_source_group_access' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+arricchisci[\\/]enrichment_response_processing\.py:\d+: unused method '_(detect_geometry_type|infer_scalar_type|flatten_dict)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+carica[\\/]+column_mapper\.py:\d+: unused method 'validate_mapping' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+carica[\\/]+writer_(base|preflight_lifecycle)\.py:\d+: unused method 'drop_table' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+carica[\\/]+writer_data_prep\.py:\d+: unused method '_fix_mixed_type_columns' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+datasources[\\/]factory\.py:\d+: unused function 'supported_types' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+datasources[\\/]service\.py:\d+: unused method 'mark_as_processed' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+estrai[\\/]query_generator\.py:\d+: unused method '_crea_condizione' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+estrai[\\/]sql_errors\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+features[\\/]+arcgis_client\.py:\d+: unused method 'update_renderer' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+features[\\/]+arcgis_auth\.py:\d+: unused method '_authenticate_(oauth2|token)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+features[\\/]+features_service\.py:\d+: unused method 'write_features' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+features[\\/]+arcgis_feature_write_methods\.py:\d+: unused method 'apply_edits' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+files[\\/]service\.py:\d+: unused (method|function) '(_load_by_format|create_file_service|_rmtree_safe|model_(dump|validate)_json)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+intermediate[\\/](policy|snapshot|node_snapshots_db)\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+manipola[\\/]executor\.py:\d+: unused method '(set_pipeline_dispatcher|_rileva_geometria_da_contenuto)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+orchestrator[\\/](execution_logs|router_mappers)\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+orchestrator[\\/]router\.py:\d+: unused method 'coerce_progress_to_int' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+orchestrator[\\/]task_service\.py:\d+: unused method '(create_carica_task|create_preview_task|get_task_result|list_active_tasks|cleanup_old_results)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+projects[\\/]service\.py:\d+: unused method '(get_project|project_exists)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+projects[\\/]source_freshness\.py:\d+: unused function 'assert_project_source_fresh' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+scheduler[\\/](concurrency_manager|db|log_service|policy_engine|runs_catalog|schedule_service)\.py:\d+: unused (method|function) "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+resources[\\/]column_types\.py:\d+: unused function "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+imdf[\\/]arcgis[\\/](publish_ops|service_discovery|sql_helpers)\.py:\d+: unused function "),

    # Model convenience methods and typed row adapters.
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+.+[\\/]models\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+projects[\\/]canvas_models\.py:\d+: unused method 'normalize_type' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+projects[\\/]canvas_models\.py:\d+: unused method '_warn_on_unknown_extras' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+projects[\\/]repository\.py:\d+: unused method 'update_canvas_key' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+admin_system_status[\\/]models\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+.+[\\/]db\.py:\d+: unused method '__getitem__' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+artifacts[\\/]types\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+dataframes[\\/](default_loader|ports)\.py:\d+: unused method 'load_(parquet|path)' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+dataframes[\\/](export_writer|geodata)\.py:\d+: unused (method|function) "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+results[\\/]streaming_blob_writer\.py:\d+: unused method '(readable|writable|seekable)' "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]+resources[\\/]metadata\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]+resources[\\/]storage_layout\.py:\d+: unused function "),

    # Small utility public APIs.
    re.compile(r"backend[\\/]+app[\\/]+db[\\/]query_engines\.py:\d+: unused method 'dispose_all' "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]clock\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+infrastructure[\\/]http[\\/]async_client\.py:\d+: unused function 'get_async_client' "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]error_codes\.py:\d+: unused function 'http_status_code' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+files[\\/]helpers\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]connections[\\/]esri_json\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]connections[\\/]oauth\.py:\d+: unused function 'invalidate_oauth2_token' "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]connections[\\/]oauth\.py:\d+: unused function '_.+_for_tests' "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]execution[\\/]clock\.py:\d+: unused method "),
    re.compile(r"backend[\\/]+app[\\/]+shared[\\/]resources[\\/]external_artifact_processor\.py:\d+: unused method 'process' "),
    re.compile(r"backend[\\/]+app[\\/]+utils[\\/]logger\.py:\d+: unused (method|function) "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+admin_backups[\\/]service\.py:\d+: unused function 'is_manual_backup_running' "),
    re.compile(r"backend[\\/]+app[\\/]+core[\\/]+auth[\\/]mfa\.py:\d+: unused function 'vacuum_expired_used_codes' "),
    re.compile(r"backend[\\/]+app[\\/]environment\.py:\d+: unused function 'is_hardened_environment' "),
    re.compile(r"backend[\\/]+workers[\\/]+tasks[\\/]+.+_task\.py:\d+: unused method "),
)


def is_allowed_vulture_finding(line: str) -> bool:
    """Return whether a Vulture finding is an approved structural false positive."""
    normalized = line.strip().replace("\\", "/")
    return any(pattern.search(normalized) for pattern in ALLOWED_VULTURE_PATTERNS)
