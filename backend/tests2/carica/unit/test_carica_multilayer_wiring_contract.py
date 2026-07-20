"""Contract: dispatch una-run verso il publish ArcGIS multi-layer.

`maybe_multilayer_arcgis_write` recupera i bytes dell'artefatto upstream, li
interroga via il registry generico e — se un adapter li riconosce (es. IMDF
bundle) — pubblica N layer. Altrimenti ritorna None (fallback mono-layer).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import app.core.carica.dispatch as ct


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]

_SERVICE_URL = "https://arc.test/svc/FeatureServer"


def _bundle_bytes() -> bytes:
    bundle = {
        "schema": "plenora.indoor.bundle",
        "version": "1.0",
        "metadata": {"site_ids": ["S1"]},
        "manifest": {
            "schema_descriptor": {
                "schema": "plenora.indoor.schema",
                "version": "1.0",
                "crs": "EPSG:4326",
                "layers": {
                    "Localita": {
                        "geometry": {"type": "Point", "has_z": False},
                        "identity": ["id"],
                        "fields": [
                            {"name": "id", "source": "id", "type": "string", "nullable": False},
                        ],
                        "renderer_ref": "Localita",
                    },
                },
            }
        },
        "renderers": {"Localita": {"renderer": {"type": "simple"}}},
        "layers": {
            "Localita": {
                "type": "FeatureCollection",
                "features": [
                    {"properties": {"id": "L1"},
                     "geometry": {"type": "Point", "coordinates": [1.0, 2.0]}},
                    {"properties": {"id": "L2"},
                     "geometry": {"type": "Point", "coordinates": [3.0, 4.0]}},
                ],
            },
        },
    }
    return json.dumps(bundle).encode("utf-8")


class _FakeWriter:
    def __init__(self):
        self.create_calls = []
        self.renderer_calls = []

    def create_service_with_layers(self, *, connection_id, service_name, layers, source_srid, folder_id):
        self.create_calls.append((service_name, tuple(l.layer_name for l in layers)))
        return {
            "success": True, "service_url": _SERVICE_URL, "item_id": "i1",
            "feature_count": sum(len(l.df) for l in layers),
            "errors": [],
            "layers": [
                {"id": i, "name": l.layer_name, "url": f"{_SERVICE_URL}/{i}",
                 "feature_count": len(l.df)}
                for i, l in enumerate(layers)
            ],
        }

    def apply_layer_renderers(self, *, connection_id, renderers):
        self.renderer_calls.append(dict(renderers))
        return {"applied": len(renderers), "errors": []}


def _patch_common(monkeypatch, fetched_bytes, fake_writer):
    monkeypatch.setattr(
        ct, "get_project_runtime_context",
        lambda pid: SimpleNamespace(group_id="g1"),
    )

    class _FakeLoader:
        def resolve_project_file_bytes(self, ref, *, expected_group_id):
            assert expected_group_id == "g1"
            return (fetched_bytes, "project_output.json") if fetched_bytes is not None else None

    monkeypatch.setattr(ct, "SourceLoader", _FakeLoader)
    monkeypatch.setattr(ct, "create_default_arcgis_feature_writer", lambda: fake_writer)
    monkeypatch.setattr(ct, "is_resource_visible", lambda *_a, **_k: True)


def test_bundle_upstream_routes_to_multilayer_publish(monkeypatch):
    fake = _FakeWriter()
    _patch_common(monkeypatch, _bundle_bytes(), fake)

    node = {
        "arcgis_connection_id": "c1",
        "arcgis_operation": "create_service",
        "arcgis_new_service_name": "MyIndoorSvc",
    }
    result = ct.maybe_multilayer_arcgis_write(
        "p1", {}, node, "t1", progress=lambda *a: None,
    )

    assert result is not None
    assert result["success"] is True
    assert result["rows_written"] == 2
    assert fake.create_calls == [("MyIndoorSvc", ("Localita",))]
    # renderer risolto per nome->URL e applicato
    assert fake.renderer_calls == [{f"{_SERVICE_URL}/0": {"renderer": {"type": "simple"}}}]


def test_non_bundle_json_falls_back_to_none(monkeypatch):
    fake = _FakeWriter()
    _patch_common(monkeypatch, b'{"schema": "qualcos.altro"}', fake)
    result = ct.maybe_multilayer_arcgis_write("p1", {}, {}, "t1", progress=lambda *a: None)
    assert result is None
    assert fake.create_calls == []


def test_non_json_artifact_falls_back_to_none(monkeypatch):
    fake = _FakeWriter()
    _patch_common(monkeypatch, b"id,name\n1,foo\n", fake)
    result = ct.maybe_multilayer_arcgis_write("p1", {}, {}, "t1", progress=lambda *a: None)
    assert result is None


def test_no_upstream_artifact_returns_none(monkeypatch):
    fake = _FakeWriter()
    _patch_common(monkeypatch, None, fake)
    result = ct.maybe_multilayer_arcgis_write("p1", {}, {}, "t1", progress=lambda *a: None)
    assert result is None


def test_missing_group_id_skips_detection(monkeypatch):
    fake = _FakeWriter()
    _patch_common(monkeypatch, _bundle_bytes(), fake)
    monkeypatch.setattr(ct, "get_project_runtime_context", lambda pid: SimpleNamespace(group_id=None))
    result = ct.maybe_multilayer_arcgis_write("p1", {}, {}, "t1", progress=lambda *a: None)
    assert result is None
