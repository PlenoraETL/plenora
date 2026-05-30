"""Contract test per persist_indoor_bundle (path nuovo plenora.indoor.bundle).

Verifica che il persist riusi il layout storage per-sezione (cosi' router GET/
list e publish lo consumano senza .gdb): manifest/validation/renderers +
{site}/{layer}.geojson + meta con has_gdb=False e discriminatore `format`.
Spia su save_bundle_files: nessun backend storage richiesto.
"""

from __future__ import annotations

import json

import pytest

from app.core.imdf import persist as imdf_persist


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _payload() -> dict:
    return {
        "schema": "plenora.indoor.bundle",
        "version": "1.0",
        "metadata": {"crs": "EPSG:4326", "site_ids": ["S1"]},
        "manifest": {
            "schema": "plenora.indoor.bundle",
            "version": "1.0",
            "layer_counts": {"Locale": 2},
            "schema_descriptor": {
                "schema": "plenora.indoor.schema",
                "version": "1.0",
                "crs": "EPSG:4326",
                "layers": {
                    "Locale": {
                        "geometry": {"type": "Polygon", "has_z": False},
                        "identity": ["id"],
                        "fields": [{"name": "id", "source": "id", "type": "string", "nullable": False}],
                    }
                },
            },
        },
        "validation": {"passed": True, "total_errors": 0, "total_warnings": 3},
        "renderers": {"Locale": {"type": "simple"}},
        "layers": {
            "Locale": {"type": "FeatureCollection", "features": [{"type": "Feature"}, {"type": "Feature"}]}
        },
    }


@pytest.fixture
def captured(monkeypatch):
    box = {}

    def _spy(task_id, files, meta, **kwargs):
        box["task_id"] = task_id
        box["files"] = files
        box["meta"] = meta
        box["kwargs"] = kwargs
        return {"file_keys": list(files), "meta_key": "k", "gdb_zip_key": None}

    monkeypatch.setattr(imdf_persist._imdf_storage, "save_bundle_files", _spy)
    return box


def test_persist_writes_section_layout(captured):
    imdf_persist.persist_indoor_bundle(_payload(), task_id="t1", project_id="p1")

    files = captured["files"]
    assert "manifest.json" in files
    assert "validation.json" in files
    assert "renderers.json" in files
    assert "S1/Locale.geojson" in files  # {site}/{layer}.geojson

    # manifest conserva lo schema_descriptor (serve a publish + validator)
    manifest = json.loads(files["manifest.json"])
    assert manifest["schema_descriptor"]["layers"]["Locale"]["identity"] == ["id"]

    # niente .gdb passato allo storage (path senza GDB)
    assert captured["kwargs"].get("gdb_zip_bytes") in (None, ...)
    assert "gdb_zip_bytes" not in captured["kwargs"] or captured["kwargs"]["gdb_zip_bytes"] is None


def test_persist_meta_discriminator_and_summary(captured):
    imdf_persist.persist_indoor_bundle(_payload(), task_id="t1", project_id="p1")
    meta = captured["meta"]

    assert meta["has_gdb"] is False
    assert meta["format"] == "plenora.indoor.bundle"
    assert meta["bundle_version"] == "1.0"
    assert meta["site_id"] == "S1"
    assert meta["site_ids"] == ["S1"]
    assert meta["project_id"] == "p1"
    assert meta["has_renderers"] is True
    assert meta["layer_counts"] == {"Locale": 2}
    assert meta["validation_summary"] == {"passed": True, "total_errors": 0, "total_warnings": 3}


def test_load_reassembles_payload_from_storage(monkeypatch):
    """load_indoor_bundle_payload ricompone il payload dalle sezioni storage."""
    manifest = {
        "schema_descriptor": {
            "schema": "plenora.indoor.schema",
            "version": "1.0",
            "layers": {"Locale": {"identity": ["id"], "fields": []}},
        },
        "site_ids": ["S1"],
    }
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature"}]}
    monkeypatch.setattr(imdf_persist._imdf_storage, "read_manifest", lambda t: manifest)
    monkeypatch.setattr(
        imdf_persist._imdf_storage, "get_meta_only",
        lambda t: {"site_id": "S1", "format": "plenora.indoor.bundle", "bundle_version": "1.0"},
    )
    monkeypatch.setattr(
        imdf_persist._imdf_storage, "read_geojson",
        lambda t, rel: fc if rel == "S1/Locale.geojson" else None,
    )
    monkeypatch.setattr(imdf_persist._imdf_storage, "read_validation", lambda t: {"passed": True})
    monkeypatch.setattr(imdf_persist._imdf_storage, "read_renderers", lambda t: None)

    payload = imdf_persist.load_indoor_bundle_payload("t1")
    assert payload is not None
    assert payload["schema"] == "plenora.indoor.bundle"
    assert payload["version"] == "1.0"
    assert payload["manifest"]["schema_descriptor"]["layers"]["Locale"]["identity"] == ["id"]
    assert payload["layers"]["Locale"] == fc


def test_load_returns_none_without_manifest(monkeypatch):
    monkeypatch.setattr(imdf_persist._imdf_storage, "read_manifest", lambda t: None)
    assert imdf_persist.load_indoor_bundle_payload("t1") is None


def test_persist_without_site_skips_geojson(captured):
    payload = _payload()
    payload["metadata"]["site_ids"] = []
    payload["manifest"].pop("site_ids", None)
    imdf_persist.persist_indoor_bundle(payload, task_id="t1", project_id=None)
    files = captured["files"]
    # senza site_id non si possono comporre i path {site}/{layer}.geojson
    assert not any(k.endswith(".geojson") for k in files)
    assert "manifest.json" in files
