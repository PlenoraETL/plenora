"""Typed boundary contracts for arricchisci API connection persistence."""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path

import pytest

from app.core.arricchisci import db as api_db
import app.core.arricchisci.api_connection_management as management


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


def _api_row(
    *,
    api_id: str = "api-1",
    group_id: str = "grp-1",
    config: dict[str, object] | None = None,
) -> api_db.ApiConnectionRow:
    return api_db.ApiConnectionRow(
        id=api_id,
        group_id=group_id,
        config=config
        or {
            "id": api_id,
            "name": "Typed API",
            "category": "custom",
            "connection_type": "manual",
            "auth": {"type": "bearer", "token": "secret"},
            "created_at": "2026-05-08T10:00:00+00:00",
        },
        created_at="2026-05-08T10:00:00+00:00",
        updated_at="2026-05-08T10:00:00+00:00",
        created_by="user-1",
    )


def _fail_legacy_wrapper(*args, **kwargs):
    raise AssertionError("service must consume ApiConnectionRow typed helpers")


def test_api_connection_db_legacy_row_serializer_symbol_removed() -> None:
    assert not hasattr(api_db, "_api_connection_row_to_legacy_dict")


def test_api_connection_management_has_no_legacy_db_wrapper_call_sites() -> None:
    source_path = Path(management.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8-sig"))
    forbidden = {
        "get_api_connection",
        "list_api_connections",
        "save_api_connection",
        "update_api_connection",
    }
    found: list[tuple[str, int]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if not isinstance(node.func.value, ast.Name):
            continue
        if node.func.value.id == "db_svc" and node.func.attr in forbidden:
            found.append((node.func.attr, node.lineno))

    assert not found


def test_api_connection_db_read_returns_typed_row(monkeypatch) -> None:
    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        assert "FROM api_connections" in sql
        assert params == ("api-1", "grp-1")
        return {
            "id": "api-1",
            "group_id": "grp-1",
            "config": '{"name": "Typed API", "auth": {"type": "none"}}',
            "created_at": "2026-05-08T10:00:00+00:00",
            "updated_at": "2026-05-08T10:00:00+00:00",
            "created_by": "user-1",
        }

    monkeypatch.setattr(api_db, "fetchone", _fetchone)

    row = api_db.get_api_connection_row("api-1", group_id="grp-1")

    assert isinstance(row, api_db.ApiConnectionRow)
    assert row.config == {"name": "Typed API", "auth": {"type": "none"}}
    assert not hasattr(api_db.ApiConnectionRow, "to_legacy_dict")


def test_api_connection_db_update_hydrates_typed_row(monkeypatch) -> None:
    executed: list[tuple[str, tuple[object, ...]]] = []

    def _execute(sql: str, params: tuple[object, ...]) -> object:
        executed.append((sql, params))
        return object()

    def _fetchone(sql: str, params: tuple[object, ...]) -> dict[str, object]:
        assert "FROM api_connections" in sql
        assert params == ("api-1",)
        return {
            "id": "api-1",
            "group_id": "grp-1",
            "config": '{"name": "Updated API"}',
            "created_at": "2026-05-08T10:00:00+00:00",
            "updated_at": "2026-05-08T11:00:00+00:00",
            "created_by": "user-1",
        }

    monkeypatch.setattr(api_db, "execute", _execute)
    monkeypatch.setattr(api_db, "fetchone", _fetchone)

    row = api_db.update_api_connection_row("api-1", {"name": "Updated API"})

    assert isinstance(row, api_db.ApiConnectionRow)
    assert row.config == {"name": "Updated API"}
    assert "UPDATE api_connections SET" in executed[0][0]
    assert executed[0][1][2] == "api-1"


def test_api_connection_service_reads_use_typed_rows(monkeypatch, api_service) -> None:
    row = _api_row()
    monkeypatch.setattr(api_db, "get_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(api_db, "list_api_connections", _fail_legacy_wrapper)
    monkeypatch.setattr(
        api_db,
        "get_api_connection_row",
        lambda api_id, group_id=None: row if api_id == "api-1" else None,
    )
    monkeypatch.setattr(api_db, "list_api_connection_rows", lambda group_id=None: [row])

    listed = api_service.list_api_connections()
    fetched = api_service.get_api_connection("api-1")
    decrypted = api_service.list_api_connections_decrypted()

    assert listed[0]["id"] == "api-1"
    assert listed[0]["auth"]["token"] == "***"
    assert fetched["group_id"] == "grp-1"
    assert fetched["auth"]["token"] == "***"
    assert decrypted["api-1"]["auth"]["token"] == "secret"


def test_api_connection_service_create_uses_typed_save_row(
    monkeypatch,
    api_service,
) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(api_db, "save_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(api_db, "get_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(management, "_encrypt_auth_in_config", lambda config: config)

    def _save_api_connection_row(**kwargs) -> api_db.ApiConnectionRow:
        captured.update(kwargs)
        return _api_row(
            api_id=kwargs["api_id"],
            group_id=kwargs["group_id"],
            config=deepcopy(kwargs["config"]),
        )

    monkeypatch.setattr(api_db, "save_api_connection_row", _save_api_connection_row)

    result = api_service.create_api_connection(
        {
            "name": "Created API",
            "group_id": "grp-1",
            "created_by": "user-1",
            "auth": {"type": "bearer", "token": "secret"},
        }
    )

    assert captured["group_id"] == "grp-1"
    assert captured["created_by"] == "user-1"
    assert result["id"] == captured["api_id"]
    assert result["auth"]["token"] == "***"


def test_api_connection_service_update_uses_typed_update_row(
    monkeypatch,
    api_service,
) -> None:
    captured: dict[str, object] = {}
    existing = _api_row(config={"name": "Old API", "auth": {"token": "old-secret"}})

    monkeypatch.setattr(api_db, "get_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(api_db, "update_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(api_db, "get_api_connection_row", lambda api_id: existing)
    monkeypatch.setattr(management, "_decrypt_auth_in_config", lambda config, **_: config)
    monkeypatch.setattr(management, "_encrypt_auth_in_config", lambda config: config)

    def _update_api_connection_row(
        api_id: str,
        config: dict[str, object],
    ) -> api_db.ApiConnectionRow:
        captured["api_id"] = api_id
        captured["config"] = deepcopy(config)
        return _api_row(api_id=api_id, config=deepcopy(config))

    monkeypatch.setattr(api_db, "update_api_connection_row", _update_api_connection_row)

    result = api_service.update_api_connection(
        "api-1",
        {"name": "Updated API", "auth": {"token": "***"}},
    )

    updated_config = captured["config"]
    assert captured["api_id"] == "api-1"
    assert updated_config["name"] == "Updated API"
    assert updated_config["auth"]["token"] == "old-secret"
    assert result["auth"]["token"] == "***"


def test_api_connection_service_delete_checks_typed_row(
    monkeypatch,
    api_service,
) -> None:
    deleted: list[str] = []

    monkeypatch.setattr(api_db, "get_api_connection", _fail_legacy_wrapper)
    monkeypatch.setattr(api_db, "get_api_connection_row", lambda api_id: _api_row())
    monkeypatch.setattr(
        api_db,
        "delete_api_connection",
        lambda api_id: deleted.append(api_id) or True,
    )

    assert api_service.delete_api_connection("api-1") is True
    assert deleted == ["api-1"]
