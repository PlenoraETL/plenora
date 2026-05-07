"""
Tests the intermediate router behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from unittest.mock import patch

import pytest

from tests2.intermediate.unit.conftest import build_client


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract, pytest.mark.regression]


def test_get_node_data_superadmin_reads_without_group_filter(superadmin_user):
    client = build_client(superadmin_user)

    with patch("app.core.intermediate.router._check_run_access", return_value={"run_id": "run-1", "group_id": "grp-99"}), \
         patch("app.core.intermediate.router.idb.get_node_table_rows", return_value=[{"id": 1}]) as get_rows, \
         patch("app.core.intermediate.router.idb.get_node_meta", return_value={"row_count": 1, "col_schema": {"id": "int"}}):
        response = client.get("/api/runs/run-1/nodes/node-1/data")

    assert response.status_code == 200
    assert response.json()["rows"] == [{"id": 1}]
    get_rows.assert_called_once_with(
        run_id="run-1",
        node_id="node-1",
        group_id=None,
        limit=100,
    )


def test_pin_run_superadmin_uses_run_group_not_profile_group(superadmin_user):
    client = build_client(superadmin_user)

    with patch("app.core.intermediate.router._check_run_access", return_value={"run_id": "run-1", "group_id": "grp-99"}), \
         patch("app.core.intermediate.router.idb.pin_run", return_value=True) as pin_run:
        response = client.post("/api/runs/run-1/pin", json={"pinned": True})

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "pinned": True}
    pin_run.assert_called_once_with("run-1", "grp-99", pinned=True)
