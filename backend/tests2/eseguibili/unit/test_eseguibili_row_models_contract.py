from __future__ import annotations

import pytest

from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import router as eseguibili_router
from app.core.eseguibili import service as eseguibili_service
from app.shared.contracts.auth import CurrentUser


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _db_row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "id": "exec-1",
        "name": "Executable",
        "description": "",
        "canvas": '{"nodes": [{"id": "source", "type": "file_source"}], "edges": []}',
        "expected_columns": '[{"name": "codice", "required": true}]',
        "default_output_format": "xlsx",
        "default_output_name": None,
        "icon": None,
        "source_canvas_id": "project-1",
        "group_id": "grp-1",
        "created_by": "u-1",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
    }
    row.update(overrides)
    return row


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def test_get_eseguibile_missing_row_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(eseguibili_db.db, "fetchone", lambda *args, **kwargs: None)

    assert eseguibili_db.get_eseguibile("missing") is None


def test_eseguibile_row_rejects_null_required_field() -> None:
    with pytest.raises(ValueError, match="name"):
        eseguibili_db.eseguibile_row_from_db(_db_row(name=None))


def test_eseguibile_row_keeps_json_compatibility_defaults() -> None:
    row = eseguibili_db.eseguibile_row_from_db(
        _db_row(canvas="{bad-json", expected_columns="{bad-json"),
    )

    assert row.canvas == {}
    assert row.expected_columns == []


def test_list_eseguibili_returns_typed_summary_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        eseguibili_db.db,
        "fetchall",
        lambda *args, **kwargs: [
            _db_row(canvas=None, expected_columns='[{"name": "a"}, {"name": "b"}]'),
        ],
    )

    rows = eseguibili_db.list_eseguibili(group_id="grp-1")

    assert rows == [
        eseguibili_db.EseguibileSummaryRow(
            id="exec-1",
            name="Executable",
            description="",
            default_output_format="xlsx",
            default_output_name=None,
            icon=None,
            source_canvas_id="project-1",
            group_id="grp-1",
            created_by="u-1",
            created_at="2026-05-08T08:00:00+00:00",
            updated_at="2026-05-08T08:00:00+00:00",
            expected_columns_count=2,
        ),
    ]


def test_router_serializes_typed_row_at_api_edge(monkeypatch) -> None:
    monkeypatch.setattr(
        eseguibili_router,
        "compute_can_edit",
        lambda record, user: record.id == "exec-1" and user.user_id == "u-1",
    )
    row = eseguibili_db.eseguibile_row_from_db(_db_row())

    payload = eseguibili_router._details_response(row, _user())

    assert payload["id"] == "exec-1"
    assert payload["expected_columns"] == [{"name": "codice", "required": True}]
    assert payload["can_edit"] is True


def test_create_from_project_returns_typed_eseguibile_row(monkeypatch) -> None:
    from app.core.projects import db as projects_db

    created = eseguibili_db.eseguibile_row_from_db(_db_row())

    monkeypatch.setattr(
        projects_db,
        "get_project",
        lambda project_id: {
            "id": project_id,
            "project_type": "arricchisci",
            "group_id": "grp-1",
            "canvas": {
                "nodes": [{"id": "source", "type": "file_source"}],
                "edges": [],
            },
        },
    )
    monkeypatch.setattr(
        eseguibili_service,
        "extract_expected_columns",
        lambda canvas: [{"name": "codice", "required": True}],
    )
    monkeypatch.setattr(
        eseguibili_service.eseguibili_db,
        "create_eseguibile",
        lambda **kwargs: created,
    )

    result = eseguibili_service.create_from_project(
        source_project_id="project-1",
        name="Executable",
        group_id="grp-1",
        created_by="u-1",
    )

    assert isinstance(result, eseguibili_db.EseguibileRow)
    assert result.id == "exec-1"
