from __future__ import annotations

from pydantic import ValidationError
import pytest

from app.core.admin_system_status import service as admin_status_service
from app.core.connections import arcgis_connection_resolver
from app.core.connections import db as connection_db
from app.core.eseguibili import db as eseguibili_db
from app.core.results import db as results_db
from app.db import tickets_db_service as tickets_db
from app.core.projects import db as project_db


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _ticket_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "ticket-1",
        "kind": "user_support",
        "title": "Broken row",
        "description": "",
        "severity": "medium",
        "status": "open",
        "category": "other",
        "created_by": "user-1",
        "assignee": None,
        "group_id": "grp-1",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
        "resolved_at": None,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


def _eseguibile_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "exec-1",
        "name": "Executable",
        "description": "",
        "canvas": '{"nodes": [], "edges": []}',
        "expected_columns": "[]",
        "default_output_format": "xlsx",
        "default_output_name": None,
        "icon": None,
        "source_canvas_id": "project-1",
        "group_id": "grp-1",
        "created_by": "user-1",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
    }
    row.update(overrides)
    return row


def _project_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "project-1",
        "group_id": "grp-1",
        "name": "Project",
        "project_type": "estrai",
        "canvas": '{"nodes": [], "edges": []}',
        "connection_id": "conn-1",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
        "created_by": "user-1",
        "retention_n": 3,
    }
    row.update(overrides)
    return row


def _connection_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "conn-1",
        "group_id": "grp-1",
        "name": "Warehouse",
        "db_type": "postgres",
        "host": "db.example.test",
        "port": 5432,
        "database": "analytics",
        "db_schema": "public",
        "username": "reader",
        "password": "secret",
        "extra": "{}",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
        "created_by": "user-1",
    }
    row.update(overrides)
    return row


def test_tickets_list_fails_on_malformed_typed_row(monkeypatch) -> None:
    monkeypatch.setattr(
        tickets_db,
        "fetchall",
        lambda *args, **kwargs: [_ticket_row(severity=None)],
    )

    with pytest.raises(ValueError, match="severity"):
        tickets_db.list_tickets(group_id="grp-1")


def test_eseguibili_source_refs_fail_on_malformed_db_row(monkeypatch) -> None:
    monkeypatch.setattr(
        eseguibili_db.db,
        "fetchall",
        lambda *args, **kwargs: [{"id": "exec-1", "name": None}],
    )

    with pytest.raises(ValueError, match="name"):
        eseguibili_db.list_eseguibili_by_source("project-1")


def test_admin_status_payload_rejects_malformed_provider_block() -> None:
    with pytest.raises(ValidationError, match="status"):
        admin_status_service._as_status_block(
            {"status": "degraded", "message": "not part of the contract"}
        )


def test_results_metadata_row_fails_on_malformed_counts() -> None:
    with pytest.raises(ValueError, match="size_bytes"):
        results_db.ResultMetadataRow.from_mapping({
            "id": "result-1",
            "size_bytes": "not-an-int",
        })


def test_connection_config_rows_fail_on_null_required_db_fields(monkeypatch) -> None:
    monkeypatch.setattr(
        connection_db,
        "fetchall",
        lambda *args, **kwargs: [_connection_row(name=None)],
    )

    with pytest.raises(ValueError, match="name"):
        connection_db.list_connection_configs("grp-1")


def test_arcgis_connection_resolution_fails_before_provider_on_malformed_row(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        connection_db,
        "fetchone",
        lambda *args, **kwargs: {
            "id": "arcgis-1",
            "server_url": None,
        },
    )
    monkeypatch.setattr(
        arcgis_connection_resolver,
        "create_arcgis_provider",
        lambda: pytest.fail("provider must not be created for malformed row"),
    )

    with pytest.raises(ValueError, match="server_url"):
        arcgis_connection_resolver.resolve_arcgis_connection("arcgis-1")


def test_arcgis_provider_factory_failure_propagates_after_typed_row(monkeypatch) -> None:
    monkeypatch.setattr(
        arcgis_connection_resolver.connection_db,
        "get_arcgis_connection_row",
        lambda connection_id: connection_db.ArcGISConnectionRow(
            id=connection_id,
            server_url="https://example.test/arcgis",
        ),
    )
    monkeypatch.setattr(
        arcgis_connection_resolver,
        "create_arcgis_provider",
        lambda: (_ for _ in ()).throw(RuntimeError("provider unavailable")),
    )

    with pytest.raises(RuntimeError, match="provider unavailable"):
        arcgis_connection_resolver.resolve_arcgis_connection("arcgis-1")


def test_project_detail_rows_fail_on_null_required_db_fields() -> None:
    with pytest.raises(ValueError, match="name"):
        project_db.ProjectDetailRow.from_mapping(_project_row(name=None))


def test_project_create_hydration_failure_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr(project_db, "execute", lambda *args, **kwargs: None)
    monkeypatch.setattr(project_db, "get_project_row", lambda project_id: None)

    with pytest.raises(RuntimeError, match="write hydration failed"):
        project_db.create_project_row(
            project_id="project-1",
            group_id="grp-1",
            name="Project",
            project_type="estrai",
            connection_id="conn-1",
            created_by="user-1",
        )
