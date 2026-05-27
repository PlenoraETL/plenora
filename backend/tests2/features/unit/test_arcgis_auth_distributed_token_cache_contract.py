"""Contract tests for ArcGIS distributed token cache."""

from __future__ import annotations

import pytest

from app.core.features.arcgis_client import ArcGISClient, ArcGISConnectionInfo
from app.shared.connections import oauth


pytestmark = [pytest.mark.unit, pytest.mark.features, pytest.mark.contract]


class _Response:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, body: dict[str, object]) -> None:
        self._body = body

    def json(self) -> dict[str, object]:
        return self._body


class _Http:
    def __init__(self, token: str) -> None:
        self.token = token
        self.post_calls = 0

    def post(self, url, data=None, timeout=30, headers=None):
        self.post_calls += 1
        return _Response({"access_token": self.token, "expires_in": 3600})

    def get(self, url, params=None, timeout=30, headers=None):
        raise AssertionError("GET is not used by this test")


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.store[key] = value

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self.store:
            return False
        self.store[key] = value
        return True

    def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _reset_token_cache_state():
    oauth._reset_token_cache_state_for_tests()
    yield
    oauth._reset_token_cache_state_for_tests()


def test_arcgis_oauth_token_is_reused_across_client_instances(monkeypatch):
    oauth._set_token_cache_client_for_tests(_FakeRedis())
    fetch_calls = []

    def _fetch_token(auth, *, extra_form_data=None):
        fetch_calls.append((auth, extra_form_data))
        return {"access_token": "arcgis-token-1", "expires_in": 3600}

    monkeypatch.setattr(oauth, "_fetch_oauth2_client_credentials_token", _fetch_token)

    conn = ArcGISConnectionInfo(
        server_url="https://tenant.maps.arcgis.com",
        server_type="arcgis_online",
        auth_type="oauth2",
        client_id="arcgis-client",
        client_secret="arcgis-secret",
    )
    first_http = _Http("arcgis-token-1")
    second_http = _Http("arcgis-token-2")

    first_client = ArcGISClient(http_client=first_http)
    second_client = ArcGISClient(http_client=second_http)

    assert first_client.authenticate(conn) == "arcgis-token-1"
    assert second_client.authenticate(conn) == "arcgis-token-1"
    assert len(fetch_calls) == 1
    assert first_http.post_calls == 0
    assert second_http.post_calls == 0
