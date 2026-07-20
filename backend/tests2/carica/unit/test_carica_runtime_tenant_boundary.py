from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.carica import dispatch
from app.core.carica.executor import CaricaExecutor


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.security]


def test_arcgis_write_denies_connection_outside_contract_tenant(monkeypatch):
    writer_factory = MagicMock()
    monkeypatch.setattr(dispatch, "create_default_arcgis_feature_writer", writer_factory)
    monkeypatch.setattr(dispatch, "is_resource_visible", lambda *_a, **_k: False)

    result = dispatch.execute_arcgis_write(
        pd.DataFrame({"id": [1]}),
        {"arcgis_connection_id": "arc-other", "arcgis_operation": "add"},
        "project-1",
        "task-1",
        progress=lambda *_a: None,
        expected_group_id="tenant-1",
    )

    assert result["success"] is False
    assert result["error"] == "arcgis_connection_not_visible"
    writer_factory.assert_not_called()


def test_datasource_uses_contract_tenant_without_project_relookup(monkeypatch):
    project_service = MagicMock()
    monkeypatch.setattr(dispatch, "_get_project_service", lambda: project_service)
    visible_calls = []
    monkeypatch.setattr(
        dispatch,
        "is_resource_visible",
        lambda table, resource_id, group_id, **_kw: visible_calls.append(
            (table, resource_id, group_id)
        ) or True,
    )
    sink = SimpleNamespace(write_chunks=lambda *_a, **_k: None)
    monkeypatch.setattr(dispatch, "_get_datasource_service", lambda: sink)

    result = dispatch.execute_datasource_write(
        pd.DataFrame({"id": [1]}),
        {"datasource_id": "ds-1", "dest_filename": "out", "dest_format": "csv"},
        "project-1",
        "task-1",
        progress=lambda *_a: None,
        expected_group_id="tenant-contract",
    )

    assert result["success"] is True
    project_service.get_project_metadata.assert_not_called()
    assert visible_calls == [("datasources", "ds-1", "tenant-contract")]


def test_db_visibility_uses_contract_tenant_without_project_relookup(monkeypatch):
    project_service = MagicMock()
    monkeypatch.setattr(
        "app.core.carica.executor.project_service_module.ProjectService",
        lambda: project_service,
    )
    visible = MagicMock(return_value=True)
    monkeypatch.setattr(
        "app.core.carica.executor.visibility_module.is_resource_visible",
        visible,
    )

    CaricaExecutor()._assert_connection_visible_for_project(
        "conn-1",
        "project-1",
        expected_group_id="tenant-contract",
    )

    project_service.get_project_metadata.assert_not_called()
    visible.assert_called_once_with(
        "connections", "conn-1", "tenant-contract", is_superadmin=False,
    )
