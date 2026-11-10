"""
Shared SQL safety helpers for database update construction.
"""

from collections.abc import Mapping


_ALLOWED_UPDATE_FIELDS: dict[str, frozenset[str]] = {
    "users": frozenset({
        "username",
        "email",
        "group_id",
        "role",
        "is_active",
        "password_hash",
        "updated_at",
    }),
    "projects": frozenset({
        "name",
        "description",
        "connection_id",
        "canvas",
        "canvas_validated",
        "output_contract",
        "updated_at",
        "retention_n",
        # Metadati modificati da PATCH /projects/{id} (service.update_project_meta):
        # senza queste voci safe_set_clause rifiutava tag e titolo della catena
        # e la PATCH rispondeva 500 (collaudo 09/11/2026).
        "tags",
        "chain_title",
    }),
    "connections": frozenset({
        "name",
        "db_type",
        "host",
        "port",
        "database",
        "username",
        "password",
        "db_schema",
        "extra",
        "is_active",
        "updated_at",
    }),
    "schedules": frozenset({
        "name",
        "description",
        "config",
        "is_enabled",
        "cron_expr",
        "last_run_at",
        "last_run_status",
        "last_run_rows",
        "next_run_at",
        "run_count",
        "error_count",
        "updated_at",
        "cron_expression",
        "last_run",
        "last_status",
        "next_run",
    }),
    "datasources": frozenset({
        "name",
        "ds_type",
        "config",
        "updated_at",
    }),
    "eseguibili": frozenset({
        "name",
        "description",
        "default_output_format",
        "default_output_name",
        "icon",
        "dashboard_uid",
        "updated_at",
    }),
    "tickets": frozenset({
        "status",
        "severity",
        "category",
        "assignee",
        "title",
        "description",
        "updated_at",
        "resolved_at",
    }),
}


def safe_set_clause(
    table: str,
    fields: Mapping[str, object],
) -> tuple[str, tuple[object, ...]]:
    allowed = _ALLOWED_UPDATE_FIELDS.get(table, frozenset())
    invalid = set(fields) - allowed
    if invalid:
        raise ValueError(
            f"Campi non consentiti per UPDATE {table}: {invalid}. "
            f"Consentiti: {allowed}"
        )
    set_clause = ", ".join(f"{k} = %s" for k in fields)
    return set_clause, tuple(fields.values())
