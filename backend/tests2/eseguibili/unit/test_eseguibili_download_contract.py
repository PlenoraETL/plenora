"""
Tests the eseguibili download behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest

from app.core.eseguibili.router import _task_owner_path, _upload_meta_path
from tests2.eseguibili.unit.conftest import build_client


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.contract, pytest.mark.regression]


def test_upload_meta_path_sanitizes_file_id(monkeypatch, tmp_path):
    import app.core.eseguibili.router as router_mod

    monkeypatch.setattr(router_mod, "settings", type("S", (), {"DATA_DIR": tmp_path, "ESEGUIBILI_OUTPUTS_DIR": tmp_path}))

    path = _upload_meta_path("../evil??id")

    assert path.name == "evilid.meta.json"


def test_upload_meta_rejects_local_path_payload():
    import app.core.eseguibili.router as router_mod

    with pytest.raises(ValueError, match="local paths"):
        router_mod._save_upload_meta(
            "fs-upload-1",
            {
                "path": "/tmp/upload.csv",
                "eseguibile_id": "exe-1",
                "user_id": "u-1",
            },
        )


def test_task_owner_path_sanitizes_task_id(monkeypatch, tmp_path):
    import app.core.eseguibili.router as router_mod

    monkeypatch.setattr(router_mod, "settings", type("S", (), {"DATA_DIR": tmp_path, "ESEGUIBILI_OUTPUTS_DIR": tmp_path}))

    path = _task_owner_path("../task??id")

    assert path.name == "taskid.json"


def test_download_fails_closed_when_owner_metadata_is_missing(current_user):
    client = build_client(current_user)

    with patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value={"id": "exe-1", "group_id": "grp-1"}), \
         patch("app.core.eseguibili.router._load_task_owner", return_value=None):
        response = client.get("/api/eseguibili/exe-1/download/task-1")

    assert response.status_code == 404


def test_download_blocks_user_mismatch_in_owner_metadata(current_user):
    client = build_client(current_user)

    with patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value={"id": "exe-1", "group_id": "grp-1"}), \
         patch("app.core.eseguibili.router._load_task_owner", return_value={"eseguibile_id": "exe-1", "user_id": "u-2"}):
        response = client.get("/api/eseguibili/exe-1/download/task-1")

    assert response.status_code == 403


def test_download_allows_superadmin_past_missing_owner_metadata(superadmin_user, tmp_path):
    client = build_client(superadmin_user)
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()

    with patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value={"id": "exe-1", "group_id": "grp-1"}), \
         patch("app.core.eseguibili.router._load_task_owner", return_value=None), \
         patch("app.core.eseguibili.router.settings", type("S", (), {"ESEGUIBILI_OUTPUTS_DIR": output_dir})):
        response = client.get("/api/eseguibili/exe-1/download/task-1")

    assert response.status_code == 404
