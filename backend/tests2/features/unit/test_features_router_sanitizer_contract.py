"""Integration contract test: sanitizer applicato sui 7 punti HIGH RISK
di ``features/routes.py`` (Batch Fix 6.2).

Punti protetti (tutti ``except ValueError``/``FileNotFoundError`` -> 404):
    - linea 210: ``browse_services``
    - linea 225: ``list_folders``
    - linea 244: ``browse_layers``
    - linea 263: ``get_layer_schema``
    - linea 302: ``query_preview``
    - linea 465: ``write_features`` (FileNotFoundError -> 404)
    - linea 512: ``create_service`` (FileNotFoundError -> 404)

Scope del test: mostrare che **almeno un** endpoint del cluster applica
effettivamente il sanitizer (``browse_services`` come rappresentante) +
blindare il ramo FileNotFoundError di ``create_service``. Non si
duplica il test per tutti e 7 i punti — la sanitize e' una singola
funzione idempotente applicata uniformemente, e il contratto del
sanitizer e' gia' coperto dall'unit test.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.core.features import routes as features_routes_module


pytestmark = [
    pytest.mark.unit,
    pytest.mark.contract,
    pytest.mark.features,
    pytest.mark.regression,
    pytest.mark.security,
]


def test_browse_services_value_error_sanitizes_detail(client, monkeypatch):
    """Il service ArcGIS solleva ValueError con URL + token: il detail
    esposto dal router deve essere sanitizzato."""
    monkeypatch.setattr(
        features_routes_module, "_check_arcgis_access", lambda *a, **kw: None,
    )

    sporco = (
        "Feature server https://tenant.arcgis.com/rest/services/FS?token=abc123 "
        "not reachable"
    )

    fake_service = MagicMock()
    fake_service.browse_services.side_effect = ValueError(sporco)
    monkeypatch.setattr(features_routes_module, "_service", fake_service)

    response = client.get("/api/features/connections/conn-1/services")

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "https://" not in detail
    assert "tenant.arcgis.com" not in detail
    assert "token=abc123" not in detail
    assert "[URL]" in detail


def test_create_service_file_not_found_sanitizes_detail(client, monkeypatch):
    """create_service cattura FileNotFoundError con path input -> 404.
    Il path raw NON deve arrivare al client."""
    monkeypatch.setattr(
        features_routes_module, "_check_arcgis_access", lambda *a, **kw: None,
    )

    sporco = r"Input file /home/urby/tmp/data/upload.csv not found"

    # Mock sia la materializzazione sia il service: serve che l'eccezione
    # venga catturata nel boundary del router, indipendentemente da quale
    # step interno fallisce.
    class _FakeMaterialized:
        def __enter__(self):
            raise FileNotFoundError(sporco)
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        features_routes_module, "_resolve_input_path",
        lambda *a, **kw: _FakeMaterialized(),
    )
    monkeypatch.setattr(features_routes_module, "_service", MagicMock())

    response = client.post(
        "/api/features/create-service",
        json={
            "connection_id": "conn-1",
            "service_name": "svc-1",
            "layer_name": "lyr-1",
            "input_path": "/home/urby/tmp/data/upload.csv",
            "geometry_column": "geom",
            "source_srid": 4326,
        },
    )

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "/home/urby" not in detail
    assert "upload.csv" in detail or "[PATH]" in detail  # la parte "upload.csv" dipende dal regex
    assert "[PATH]" in detail
