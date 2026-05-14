"""
Contract coverage for `_delete_existing_service`

Counters remain owned by the provider delete boundary; IMDF keeps the
discovery/timing policy and returns the legacy `(attempted, succeeded)` shape.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.core.imdf.arcgis.service_lifecycle import _delete_existing_service


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


class _FakeConn:
    def __init__(self) -> None:
        self.server_url = "https://example.com/portal/sharing/rest"
        self.username = "u"


class _FakeClient:
    def __init__(self, result: tuple[int, int]) -> None:
        self.result = result
        self.calls: list[tuple[object, list[dict], str]] = []

    def delete_feature_service_items(
        self,
        conn: object,
        items: list[dict],
        service_name: str = "",
    ) -> tuple[int, int]:
        self.calls.append((conn, items, service_name))
        return self.result


def test_delete_existing_service_happy_path_returns_one_one() -> None:
    client = _FakeClient((1, 1))
    conn = _FakeConn()
    items = [{"id": "item-abc", "ownerFolder": "root"}]

    with patch(
        "app.core.connections.arcgis_service_lifecycle.search_user_feature_service_items",
        return_value=items,
    ):
        result = _delete_existing_service(client, conn, "MyFS")

    assert result == (1, 1)
    assert client.calls == [(conn, items, "MyFS")]


def test_delete_existing_service_partial_fail_returns_one_zero() -> None:
    client = _FakeClient((1, 0))
    conn = _FakeConn()
    items = [{"id": "item-xyz", "ownerFolder": None}]

    with patch(
        "app.core.connections.arcgis_service_lifecycle.search_user_feature_service_items",
        return_value=items,
    ):
        result = _delete_existing_service(client, conn, "MyFS")

    assert result == (1, 0)
    assert client.calls == [(conn, items, "MyFS")]
