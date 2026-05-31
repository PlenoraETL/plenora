"""Contract: scrittura ArcGIS multi-layer dal boundary CARICA (GENERICA).

`execute_carica_arcgis_multilayer_write` instrada N `ArcGISLayerSpec` verso le
capability multi-layer del writer condiviso (`create_service_with_layers` /
`write_layers_to_existing_service` / `apply_layer_renderers`), aggrega i conteggi
e applica i renderer best-effort. Nessuna conoscenza IMDF: il chiamante fornisce
layer + policy.
"""

from __future__ import annotations

import pytest

from app.core.carica.arcgis_write_boundary import (
    CaricaArcGISMultiLayerRequest,
    execute_carica_arcgis_multilayer_write,
)
from app.core.connections.arcgis_writer_boundary import ArcGISLayerSpec


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]

_SERVICE_URL = "https://arc.test/svc/FeatureServer"


class _FakeWriter:
    """Registra le chiamate ai 3 metodi multi-layer; nessun ArcGIS reale."""

    def __init__(self, *, success: bool = True, errors: list | None = None):
        self._success = success
        self._errors = errors or []
        self.create_calls: list[tuple] = []
        self.existing_calls: list[tuple] = []
        self.renderer_calls: list[dict] = []
        self.renderer_raises = False

    def create_service_with_layers(
        self, *, connection_id, service_name, layers, source_srid, folder_id
    ):
        self.create_calls.append((connection_id, service_name, tuple(l.layer_name for l in layers)))
        return {
            "success": self._success,
            "service_url": _SERVICE_URL,
            "item_id": "item-1",
            "feature_count": sum(len(l.df) for l in layers),
            "errors": self._errors,
            "layers": [
                {"id": i, "name": l.layer_name, "url": f"{_SERVICE_URL}/{i}",
                 "feature_count": len(l.df)}
                for i, l in enumerate(layers)
            ],
        }

    def write_layers_to_existing_service(
        self, *, connection_id, service_url, layers, mode, source_srid
    ):
        self.existing_calls.append((connection_id, service_url, mode, tuple(l.layer_name for l in layers)))
        return {
            "success": self._success,
            "service_url": service_url,
            "feature_count": sum(len(l.df) for l in layers),
            "errors": self._errors,
            "layers": [
                {"id": i, "name": l.layer_name, "url": f"{service_url}/{i}",
                 "feature_count": len(l.df)}
                for i, l in enumerate(layers)
            ],
        }

    def apply_layer_renderers(self, *, connection_id, renderers):
        if self.renderer_raises:
            raise RuntimeError("renderer boom")
        self.renderer_calls.append(dict(renderers))
        return {"applied": len(renderers), "errors": []}


def _layers():
    return [
        ArcGISLayerSpec("Localita", [1, 2, 3]),
        ArcGISLayerSpec("Livello", [1, 2]),
        ArcGISLayerSpec("Locale", [1], match_column="id"),
    ]


def test_create_service_routes_to_create_with_layers_and_aggregates():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1",
        operation="create_service", service_name="MySvc",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )

    assert out.success is True
    assert out.status == "completed"
    assert out.rows_written == 6  # 3+2+1
    assert out.rows_inserted == 6 and out.rows_updated == 0
    assert out.service_url == _SERVICE_URL
    assert out.item_id == "item-1"
    assert fake.create_calls == [("conn-1", "MySvc", ("Localita", "Livello", "Locale"))]
    assert fake.existing_calls == []


def test_append_routes_to_existing_service_with_mode():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1",
        operation="append", service_url=_SERVICE_URL,
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )

    assert out.success is True
    assert out.rows_written == 6
    assert out.rows_inserted == 6 and out.rows_updated == 0
    assert fake.existing_calls == [("conn-1", _SERVICE_URL, "append", ("Localita", "Livello", "Locale"))]
    assert fake.create_calls == []


def test_upsert_counts_as_updated():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1",
        operation="upsert", service_url=_SERVICE_URL,
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is True
    assert out.rows_inserted == 0 and out.rows_updated == 6


def test_renderers_resolved_by_name_to_url_and_applied():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1",
        operation="create_service", service_name="MySvc",
        # renderer keyed by LAYER NAME; risolto in URL dal risultato del write
        renderers={"Localita": {"renderer": {"a": 1}}, "Locale": {"renderer": {"b": 2}}},
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is True
    # Localita->/0, Locale->/2 (ordine dei layer); Livello (/1) senza renderer
    assert fake.renderer_calls == [{
        f"{_SERVICE_URL}/0": {"renderer": {"a": 1}},
        f"{_SERVICE_URL}/2": {"renderer": {"b": 2}},
    }]


def test_renderer_failure_is_best_effort_and_does_not_fail_write():
    fake = _FakeWriter()
    fake.renderer_raises = True
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1",
        operation="create_service", service_name="MySvc",
        renderers={"Localita": {"renderer": {}}},
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is True  # write succeeded; renderer error swallowed


def test_missing_connection_is_validation_error():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="", operation="create_service", service_name="X",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is False and out.status == "validation_error"


def test_empty_layers_is_validation_error():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1", operation="create_service", service_name="X",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=[], request=req, feature_writer=fake,
    )
    assert out.success is False and out.status == "validation_error"


def test_missing_service_name_on_create_is_validation_error():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1", operation="create_service", service_name="",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is False and out.status == "validation_error"


def test_missing_service_url_on_existing_is_validation_error():
    fake = _FakeWriter()
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1", operation="append", service_url="",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is False and out.status == "validation_error"


def test_writer_reported_failure_becomes_write_error_with_errors():
    fake = _FakeWriter(success=False, errors=["createService fallito"])
    req = CaricaArcGISMultiLayerRequest(
        project_id="p1", connection_id="conn-1", operation="create_service", service_name="X",
    )
    out = execute_carica_arcgis_multilayer_write(
        layers=_layers(), request=req, feature_writer=fake,
    )
    assert out.success is False
    assert out.status == "write_error"
    assert out.error == "carica_arcgis_write_failed"
    assert "createService fallito" in out.error_message
