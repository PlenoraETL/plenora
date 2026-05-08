"""Contract tests for ArcGIS service-name availability boundary."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.features.arcgis_client import ArcGISClient


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _FakeResponse:
    status_code = 200
    headers: dict[str, str] = {}

    def __init__(self, body: dict) -> None:
        self._body = body

    def json(self) -> dict:
        return self._body


@pytest.fixture
def conn() -> SimpleNamespace:
    return SimpleNamespace(
        server_url="https://org.maps.arcgis.com",
        username="publisher",
    )


@pytest.fixture
def client() -> ArcGISClient:
    fake_http = MagicMock(name="HttpClient")
    arcgis_client = ArcGISClient(http_client=fake_http)
    arcgis_client.authenticate = MagicMock(return_value="tok-123")
    return arcgis_client


def test_is_service_name_available_returns_true_for_available_response(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._http.get.return_value = _FakeResponse({"available": True})

    available, error = client.is_service_name_available(conn, "imdf_demo")

    assert (available, error) == (True, None)
    call = client._http.get.call_args
    assert call.args[0] == (
        "https://org.maps.arcgis.com/sharing/rest/portals/self/"
        "isServiceNameAvailable"
    )
    assert call.kwargs["params"] == {
        "name": "imdf_demo",
        "type": "Feature Service",
        "f": "json",
    }
    assert call.kwargs["headers"]["Authorization"] == "Bearer tok-123"
    assert call.kwargs["headers"]["Referer"] == conn.server_url


def test_is_service_name_available_returns_false_for_unavailable_response(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._http.get.return_value = _FakeResponse({"available": False})

    available, error = client.is_service_name_available(conn, "imdf_reserved")

    assert (available, error) == (False, None)


def test_is_service_name_available_fail_opens_for_error_shaped_response(
    client: ArcGISClient,
    conn: SimpleNamespace,
) -> None:
    client._http.get.return_value = _FakeResponse(
        {"error": {"code": 400, "message": "service name check unavailable"}}
    )

    available, error = client.is_service_name_available(conn, "imdf_demo")

    assert available is True
    assert error == "check fallito: service name check unavailable"


def test_imdf_compat_helper_delegates_to_generic_provider_method(
    conn: SimpleNamespace,
) -> None:
    from app.core.imdf.arcgis.service_discovery import _is_service_name_available

    fake_client = MagicMock(name="ArcGISProvider")
    fake_client.is_service_name_available.return_value = (False, None)

    assert _is_service_name_available(fake_client, conn, "imdf_demo") == (False, None)
    fake_client.is_service_name_available.assert_called_once_with(conn, "imdf_demo")


def test_imdf_compat_helper_preserves_fail_open_on_provider_exception(
    conn: SimpleNamespace,
) -> None:
    from app.core.imdf.arcgis.service_discovery import _is_service_name_available

    fake_client = MagicMock(name="ArcGISProvider")
    fake_client.is_service_name_available.side_effect = RuntimeError("boom")

    available, error = _is_service_name_available(fake_client, conn, "imdf_demo")

    assert available is True
    assert error == "boom"
