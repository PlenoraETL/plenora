import json

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

    import app.db.db_service as db_service
    # BE-GO-READY-AUTH-ENGINE-020: monkeypatch via consuming
    # router-module surface (mirrors connections/datasources/
    # eseguibili pattern). The IMDF router now imports
    # `assert_resource_visible` from the leaf at module top, so
    # patching the facade no longer reaches the call site.
    import app.core.imdf.router as imdf_router_mod

    monkeypatch.setattr(db_service, "get_project", lambda project_id: {"id": project_id, "group_id": "grp-1"})

    def _deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="Accesso negato")

    monkeypatch.setattr(imdf_router_mod, "assert_resource_visible", _deny)

    with pytest.raises(HTTPException) as exc:
        _assert_bundle_group_access(bundle_dir, current_user)

    assert exc.value.status_code == 403
