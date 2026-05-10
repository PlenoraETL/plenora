"""
Contract test Bug #23 — `GET /api/runs/.../nodes/.../data` deve

sanitizzare NaN / +Inf / -Inf prima di restituire JSON.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tests2.intermediate.unit.conftest import build_client


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract, pytest.mark.regression]


def test_get_node_data_returns_200_when_rows_contain_nan(superadmin_user):
    """Previous behavior: 500. Current contract: 200, NaN -> null."""
    rows_with_nan = [
        {"id": 1, "value": float("nan"), "name": "alpha"},
        {"id": 2, "value": 3.14,         "name": "beta"},
    ]

    client = build_client(superadmin_user)
    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "run-1", "group_id": "grp-1"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=rows_with_nan,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value={"row_count": 2, "col_schema": {"id": "int", "value": "float"}},
    ):
        response = client.get("/api/runs/run-1/nodes/n-1/data")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["row_count"] == 2
    assert len(body["rows"]) == 2
    # NaN -> null nel JSON
    assert body["rows"][0]["value"] is None
    # Float finiti preservati
    assert body["rows"][1]["value"] == 3.14
    # Campi non-numerici intatti
    assert body["rows"][0]["name"] == "alpha"
    assert body["rows"][0]["id"] == 1


def test_get_node_data_returns_200_when_rows_contain_pos_inf_and_neg_inf(superadmin_user):
    rows = [
        {"id": 1, "metric": float("inf")},
        {"id": 2, "metric": float("-inf")},
        {"id": 3, "metric": 0.0},
    ]

    client = build_client(superadmin_user)
    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=rows,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value={"row_count": 3, "col_schema": {}},
    ):
        response = client.get("/api/runs/r/nodes/n/data")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["metric"] is None
    assert body["rows"][1]["metric"] is None
    assert body["rows"][2]["metric"] == 0.0


def test_get_node_data_recurses_into_nested_lists_and_dicts(superadmin_user):
    """NaN dentro list/dict annidati (es. risultato di explode/aggregate
    che ha lasciato NaN nella value column) deve essere sanitizzato."""
    rows = [
        {
            "id": 1,
            "nested": {"a": float("nan"), "b": 1.5},
            "tags": [1.0, float("inf"), 2.0],
        },
    ]

    client = build_client(superadmin_user)
    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=rows,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value={"row_count": 1, "col_schema": {}},
    ):
        response = client.get("/api/runs/r/nodes/n/data")

    assert response.status_code == 200
    body = response.json()
    assert body["rows"][0]["nested"]["a"] is None
    assert body["rows"][0]["nested"]["b"] == 1.5
    assert body["rows"][0]["tags"] == [1.0, None, 2.0]


def test_get_node_data_response_is_strict_json_parseable(superadmin_user):
    """Sanity: la response deve essere parsabile da `json.loads`
    standard library (allow_nan=False di default sotto starlette)."""
    rows = [{"v": float("nan")}, {"v": float("inf")}]

    client = build_client(superadmin_user)
    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=rows,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value={"row_count": 2, "col_schema": {}},
    ):
        response = client.get("/api/runs/r/nodes/n/data")

    assert response.status_code == 200
    # Strict json roundtrip senza allow_nan
    parsed = json.loads(response.content)
    assert all(r["v"] is None for r in parsed["rows"])


def test_sanitize_helper_on_floats_dict_list():
    from app.core.intermediate.router import _sanitize_for_json

    out = _sanitize_for_json({
        "a": float("nan"),
        "b": float("inf"),
        "c": float("-inf"),
        "d": 1.5,
        "e": [float("nan"), 0, "ok"],
        "f": {"nested": float("nan")},
        "g": None,
        "h": "string",
        "i": 42,
        "j": True,
    })
    assert out["a"] is None
    assert out["b"] is None
    assert out["c"] is None
    assert out["d"] == 1.5
    assert out["e"] == [None, 0, "ok"]
    assert out["f"]["nested"] is None
    assert out["g"] is None
    assert out["h"] == "string"
    assert out["i"] == 42
    assert out["j"] is True


def test_get_node_data_clean_dataframe_unchanged(superadmin_user):
    """Regression: rows senza non-finite restano identiche."""
    rows = [
        {"id": 1, "v": 1.5, "n": "alpha"},
        {"id": 2, "v": 2.5, "n": "beta"},
    ]

    client = build_client(superadmin_user)
    with patch(
        "app.core.intermediate.router._check_run_access",
        return_value={"run_id": "r", "group_id": "g"},
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_table_rows",
        return_value=rows,
    ), patch(
        "app.core.intermediate.router.node_snapshots_db.get_node_meta",
        return_value={"row_count": 2, "col_schema": {}},
    ):
        response = client.get("/api/runs/r/nodes/n/data")

    assert response.status_code == 200
    assert response.json()["rows"] == rows
