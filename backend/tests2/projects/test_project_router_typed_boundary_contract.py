from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.shared.contracts.auth import CurrentUser
from app.core.projects.canvas_models import CanvasState


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def _metadata(project_id: str = "proj-1") -> SimpleNamespace:
    payload = {
        "id": project_id,
        "name": "Pipeline",
        "description": "",
        "project_type": "estrai",
        "default_connection_id": "conn-1",
        "group_id": "grp-1",
        "created_at": datetime(2026, 5, 8, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 5, 8, tzinfo=timezone.utc),
    }
    metadata = SimpleNamespace(**payload)
    metadata.model_dump = lambda: dict(payload)
    return metadata


def test_project_router_list_get_and_update_stay_off_legacy_get_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.projects import db as project_db
    from app.core.projects import router as router_mod

    service = MagicMock()
    service.get_project.side_effect = AssertionError(
        "project router must not serialize via service.get_project compat dict"
    )
    service.list_projects.return_value = SimpleNamespace(projects=[_metadata()])
    service.get_project_metadata.return_value = _metadata()
    service.get_project_canvas.return_value = CanvasState()
    service.get_project_output_contract.return_value = None
    service.update_canvas.return_value = True

    monkeypatch.setattr(router_mod, "_project_service", service)
    monkeypatch.setattr(router_mod, "group_filter", lambda current_user: "grp-1")
    monkeypatch.setattr(router_mod, "assert_group_access", lambda *a, **kw: None)
    assert not hasattr(project_db, "get_project")

    listed = router_mod.list_projects(current_user=_user())
    detail = router_mod.get_project("proj-1", current_user=_user())
    updated = router_mod.update_project_canvas(
        "proj-1",
        router_mod.CanvasWithPlugins(nodes=[], edges=[]),
        current_user=_user(),
    )

    assert listed["projects"][0]["id"] == "proj-1"
    # `applied_migrations` aggiunto dal #2 (trasparenza rename auto-applicati):
    # il GET espone i rename di colonna applicati in fase di sync canvas.
    assert set(detail) == {"metadata", "canvas", "output_contract", "applied_migrations"}
    assert detail["metadata"]["id"] == "proj-1"
    assert updated == {"status": "ok", "project_id": "proj-1"}
    service.get_project.assert_not_called()


def test_project_router_uses_typed_metadata_attributes_only() -> None:
    from app.core.projects import router as router_mod

    source = inspect.getsource(router_mod)

    # RAW: expression-pattern (isinstance call with two positional args) — no AST helper covers this exact shape
    assert 'isinstance(metadata, dict)' not in source
    # RAW: dict-access via .get() with a string-literal key — not a bare identifier, keep as substring guard
    assert 'metadata.get("group_id")' not in source
