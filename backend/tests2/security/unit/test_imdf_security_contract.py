"""
Tests the imdf security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import json
import ast
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.core.imdf.router import _assert_bundle_group_access


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


def test_assert_bundle_group_access_fails_closed_without_metadata(tmp_path, current_user):
    bundle_dir = tmp_path / "bundle-1"
    bundle_dir.mkdir()

    with pytest.raises(HTTPException) as exc:
        _assert_bundle_group_access(bundle_dir, current_user)

    assert exc.value.status_code == 403


def test_assert_bundle_group_access_fails_closed_without_project_id(tmp_path, current_user):
    bundle_dir = tmp_path / "bundle-1"
    bundle_dir.mkdir()
    (bundle_dir / "plenora_metadata.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(HTTPException) as exc:
        _assert_bundle_group_access(bundle_dir, current_user)

    assert exc.value.status_code == 403


def test_assert_bundle_group_access_requires_visible_project(tmp_path, current_user, monkeypatch):
    bundle_dir = tmp_path / "bundle-1"
    bundle_dir.mkdir()
    (bundle_dir / "plenora_metadata.json").write_text(
        json.dumps({"project_id": "proj-1"}),
        encoding="utf-8",
    )

    # BE-GO-READY-AUTH-ENGINE-020: monkeypatch via consuming
    # router-module surface (mirrors connections/datasources/
    # eseguibili pattern). The IMDF router now imports
    # `assert_resource_visible` from the leaf at module top, so
    # patching the facade no longer reaches the call site.
    import app.core.imdf.router as imdf_router_mod
    import app.core.imdf.project_access as imdf_project_access

    monkeypatch.setattr(
        imdf_project_access,
        "get_project_for_bundle",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Accesso negato")

    monkeypatch.setattr(imdf_router_mod, "assert_resource_visible", _deny)

    with pytest.raises(HTTPException) as exc:
        _assert_bundle_group_access(bundle_dir, current_user)

    assert exc.value.status_code == 403


def test_imdf_router_does_not_import_project_db_directly():
    router_path = Path(__file__).parents[3] / "app" / "core" / "imdf" / "router.py"
    tree = ast.parse(router_path.read_text(encoding="utf-8"))

    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.projects.db":
            violations.append((node.lineno, node.module))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "app.core.projects.db":
                    violations.append((node.lineno, alias.name))

    assert violations == []
