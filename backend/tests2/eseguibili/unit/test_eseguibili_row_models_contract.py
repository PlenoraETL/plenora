from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

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
    monkeypatch.setattr(eseguibili_db, "_fetchone", lambda *args, **kwargs: None)

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
        eseguibili_db,
        "_fetchall",
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


def test_eseguibile_rows_do_not_own_legacy_dict_serialization() -> None:
    assert not hasattr(eseguibili_db.EseguibileRow, "to_legacy_dict")
    assert not hasattr(eseguibili_db.EseguibileSummaryRow, "to_legacy_dict")
    assert not hasattr(eseguibili_db.EseguibileSourceRefRow, "to_legacy_dict")


def test_router_serializes_summary_row_at_api_edge(monkeypatch) -> None:
    monkeypatch.setattr(eseguibili_router, "compute_can_edit", lambda *args: False)
    row = eseguibili_db.EseguibileSummaryRow(
        id="exec-1",
        name="Executable",
        description="",
        default_output_format="xlsx",
        default_output_name="out.xlsx",
        icon="table",
        source_canvas_id="project-1",
        group_id="grp-1",
        created_by="u-1",
        created_at="2026-05-08T08:00:00+00:00",
        updated_at="2026-05-08T08:00:00+00:00",
        expected_columns_count=2,
    )

    payload = eseguibili_router._summary_response(row, _user())

    assert payload == {
        "id": "exec-1",
        "name": "Executable",
        "description": "",
        "default_output_format": "xlsx",
        "default_output_name": "out.xlsx",
        "icon": "table",
        "source_canvas_id": "project-1",
        "group_id": "grp-1",
        "created_by": "u-1",
        "created_at": "2026-05-08T08:00:00+00:00",
        "updated_at": "2026-05-08T08:00:00+00:00",
        "expected_columns_count": 2,
        "project_type": "arricchisci",
        "active_version": 1,
        "inputs_count": 1,
        "has_manifest": False,
        "source_changed": None,
        "binding_warnings": [],
        # Dashboard Grafana (11/11/2026): funzione spenta nei test.
        "has_dashboard": False,
        "dashboard_uid": None,
        "dashboard_feature_enabled": False,
        "can_edit": False,
    }


def test_summary_has_manifest_is_the_detail_rule(monkeypatch) -> None:
    """UI conferma U14: la card di /run sceglie modulo o file con la stessa
    regola della finestra di esecuzione (dettaglio: ``manifest`` della
    versione attiva non nullo), non con ``expected_columns_count``."""
    captured: list[str] = []
    monkeypatch.setattr(eseguibili_db, "_fetchall", lambda sql, *a, **k: captured.append(sql) or [])
    eseguibili_db.list_eseguibili(group_id="grp-1")
    # Conferma finale G5: la regola unica (manifest NON VUOTO), non IS NOT NULL.
    assert f"{eseguibili_db.MANIFEST_PRESENT_SQL} AS has_manifest" in captured[0]
    assert "(v.manifest IS NOT NULL) AS has_manifest" not in captured[0]
    assert "LEFT JOIN eseguibile_versions v ON v.id = e.active_version_id" in captured[0]

    with_manifest = eseguibili_db.eseguibile_summary_row_from_db(_db_row(has_manifest=True))
    without = eseguibili_db.eseguibile_summary_row_from_db(_db_row(has_manifest=None))
    assert (with_manifest.has_manifest, without.has_manifest) == (True, False)
    assert eseguibili_router._summary_response(with_manifest, _user())["has_manifest"] is True
    # Dettaglio: stessa colonna (manifest della versione attiva).
    detail = eseguibili_db.eseguibile_row_from_db(_db_row(active_manifest='{"inputs": []}'))
    assert detail.manifest is not None
    assert eseguibili_db.eseguibile_row_from_db(_db_row()).manifest is None


def test_create_from_project_returns_typed_eseguibile_row(monkeypatch) -> None:
    from app.core.projects import access_context

    created = eseguibili_db.eseguibile_row_from_db(_db_row())

    monkeypatch.setattr(
        access_context,
        "load_project_publish_context",
        lambda project_id: SimpleNamespace(
            project_id=project_id,
            project_type="arricchisci",
            group_id="grp-1",
            canvas={
                "nodes": [{"id": "source", "type": "file_source"}],
                "edges": [],
            },
        ),
    )
    monkeypatch.setattr(
        "app.core.eseguibili.manifest.extract_expected_columns",
        lambda _canvas: [{"name": "codice", "required": True}],
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


def test_eseguibili_service_uses_project_access_boundary_not_projects_db_get_project() -> None:
    path = Path(eseguibili_service.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.projects.db":
            names = {alias.name for alias in node.names}
            if "get_project" in names:
                violations.append("from app.core.projects.db import get_project")
        elif isinstance(node, ast.Import):
            if any(alias.name == "app.core.projects.db" for alias in node.names):
                violations.append("import app.core.projects.db")

    assert not violations, (
        "eseguibili.service must load project data through "
        "app.core.projects.access_context, not direct projects.db.get_project: "
        f"{violations}"
    )
