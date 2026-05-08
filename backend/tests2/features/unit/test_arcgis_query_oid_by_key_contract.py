"""Contract tests for generic ArcGIS OBJECTID resolution."""

from __future__ import annotations

import pytest

from app.core.features.arcgis_client import ArcGISClient
from app.shared.connections.arcgis_connection import ArcGISConnectionInfo


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class FakeHttpClient:
    def __init__(self, responses: list[dict | Exception]) -> None:
        self.responses = responses
        self.get_calls: list[dict] = []

    def get(
        self,
        url: str,
        params: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> FakeResponse:
        self.get_calls.append({
            "url": url,
            "params": params or {},
            "timeout": timeout,
            "headers": headers or {},
        })
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return FakeResponse(response)

    def post(
        self,
        url: str,
        data: dict = None,
        timeout: int = 30,
        headers: dict = None,
    ) -> FakeResponse:
        raise AssertionError("query_oid_by_key must not make POST calls")


def _public_conn() -> ArcGISConnectionInfo:
    return ArcGISConnectionInfo(
        server_url="https://example.test/arcgis",
        server_type="arcgis_online",
        auth_type="public",
    )


def test_query_oid_by_key_builds_arcgis_where_and_returns_string_keyed_oids() -> None:
    http = FakeHttpClient([
        {
            "features": [
                {"attributes": {"code": "A'1", "OBJECTID": "11"}},
                {"attributes": {"code": 7, "objectid": 12}},
                {"attributes": {"code": True, "OBJECTID": 13}},
                {"attributes": {"code": None, "OBJECTID": 14}},
            ]
        }
    ])
    client = ArcGISClient(http_client=http)

    oid_by_key, errors = client.query_oid_by_key(
        _public_conn(),
        "https://example.test/arcgis/rest/services/site/FeatureServer/0",
        "code",
        ["A'1", 7, True, None],
    )

    assert errors == []
    assert oid_by_key == {"A'1": 11, "7": 12, "True": 13}
    assert http.get_calls == [
        {
            "url": "https://example.test/arcgis/rest/services/site/FeatureServer/0/query",
            "params": {
                "where": "code IN ('A''1',7,1,NULL)",
                "outFields": "OBJECTID,code",
                "returnGeometry": "false",
                "f": "json",
            },
            "timeout": 30,
            "headers": {"Referer": "https://example.test/arcgis"},
        }
    ]


def test_query_oid_by_key_chunks_values_at_arcgis_safe_batch_size() -> None:
    first_batch_features = [
        {"attributes": {"code": value, "OBJECTID": value + 1000}}
        for value in range(200)
    ]
    second_batch_features = [
        {"attributes": {"code": 200, "OBJECTID": 1200}},
    ]
    http = FakeHttpClient([
        {"features": first_batch_features},
        {"features": second_batch_features},
    ])
    client = ArcGISClient(http_client=http)

    oid_by_key, errors = client.query_oid_by_key(
        _public_conn(),
        "https://example.test/layer/0",
        "code",
        list(range(201)),
    )

    assert errors == []
    assert len(http.get_calls) == 2
    assert http.get_calls[0]["params"]["where"].startswith("code IN (0,1,2")
    assert http.get_calls[0]["params"]["where"].endswith(",199)")
    assert http.get_calls[1]["params"]["where"] == "code IN (200)"
    assert oid_by_key["0"] == 1000
    assert oid_by_key["199"] == 1199
    assert oid_by_key["200"] == 1200


def test_query_oid_by_key_returns_partial_mapping_with_batch_errors() -> None:
    http = FakeHttpClient([
        {"features": [{"attributes": {"code": "A", "OBJECTID": 1}}]},
        RuntimeError("timeout"),
    ])
    client = ArcGISClient(http_client=http)

    oid_by_key, errors = client.query_oid_by_key(
        _public_conn(),
        "https://example.test/layer/0",
        "code",
        ["A", *range(200)],
    )

    assert oid_by_key == {"A": 1}
    assert errors == ["query OBJECTID batch 2: timeout"]
    assert len(http.get_calls) == 2
