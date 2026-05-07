"""
Tests the eseguibili execute security behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest

from tests2.eseguibili.unit.conftest import build_client
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.eseguibili, pytest.mark.security, pytest.mark.regression]


def test_execute_eseguibile_blocks_upload_owned_by_other_user():
    client = build_client(build_current_user())

    with patch("app.core.eseguibili.router.eseguibili_db.get_eseguibile", return_value={"id": "exe-1", "group_id": "grp-1"}), \
         patch("app.core.eseguibili.router._load_upload_meta", return_value={"eseguibile_id": "exe-1", "user_id": "u-2", "path": "/tmp/file.csv"}), \
         patch("app.core.eseguibili.router.eseguibili_service.execute") as execute:
        response = client.post(
            "/api/eseguibili/exe-1/execute",
            json={
                "file_id": "file-1",
                "column_mapping": [],
            },
        )

    assert response.status_code == 403
    execute.assert_not_called()
