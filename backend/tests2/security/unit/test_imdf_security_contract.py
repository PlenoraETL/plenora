"""
Tests the imdf security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.core.imdf.bundle_access import assert_bundle_group_access_from_meta


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def fake_logger():
    return SimpleNamespace(warning=lambda *a, **k: None, error=lambda *a, **k: None)


def test_assert_bundle_group_access_fails_closed_without_metadata(current_user, fake_logger):
    with pytest.raises(HTTPException) as exc:
        assert_bundle_group_access_from_meta(
            None,
            current_user,
            "bundle-1",
            logger=fake_logger,
        )

    assert exc.value.status_code == 403


def test_assert_bundle_group_access_fails_closed_without_project_id(current_user, fake_logger):
    with pytest.raises(HTTPException) as exc:
        assert_bundle_group_access_from_meta(
            {},
            current_user,
            "bundle-1",
            logger=fake_logger,
        )

    assert exc.value.status_code == 403


def test_assert_bundle_group_access_requires_visible_project(
    current_user,
    fake_logger,
    monkeypatch,
):
    import app.core.imdf.project_access as imdf_project_access

    monkeypatch.setattr(
        imdf_project_access,
        "get_project_for_bundle",
        lambda project_id: {"id": project_id, "group_id": "grp-1"},
    )

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Accesso negato")

    with pytest.raises(HTTPException) as exc:
        assert_bundle_group_access_from_meta(
            {"project_id": "proj-1"},
            current_user,
            "bundle-1",
            logger=fake_logger,
            visible_checker=_deny,
        )

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
