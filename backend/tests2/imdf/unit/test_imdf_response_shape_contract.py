"""Contract: shape HTTP dei payload IMDF esposti al frontend."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.core.imdf.router as imdf_router
from app.core.auth.auth import get_current_user
from app.core.imdf.router import router
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _build_client() -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: build_current_user(
        role="superadmin"
    )
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def imdf_bundles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "imdf_bundles"
    root.mkdir()

    bundle = root / "task-shape"
    bundle.mkdir()

    _write_json(
        bundle / "plenora_metadata.json",
        {
            "project_id": "proj-shape",
            "site_id": "SITE123",
            "saved_at": "2026-04-15T10:00:00",
            "building_count": 2,
            "level_count": 3,
            "unit_count": 5,
            "has_gdb": True,
        },
    )
    _write_json(bundle / "manifest.json", {"id": "SITE123", "version": "1.0"})
    _write_json(bundle / "validation.json", {"ok": True, "errors": []})
    _write_json(bundle / "renderers.json", {"layers": ["unit"]})

    site_dir = bundle / "SITE123"
    site_dir.mkdir()
    _write_json(
        site_dir / "building.geojson",
        {"type": "FeatureCollection", "features": [{"type": "Feature"}] * 2},
    )
    _write_json(
        site_dir / "level.geojson",
        {"type": "FeatureCollection", "features": [{"type": "Feature"}] * 3},
    )
    _write_json(
        site_dir / "unit.geojson",
        {"type": "FeatureCollection", "features": [{"type": "Feature"}] * 5},
    )

    (bundle / "SITE123.gdb").mkdir()
    (bundle / "source.ifc").write_text("IFC", encoding="utf-8")

    fake_settings = SimpleNamespace(IMDF_BUNDLES_DIR=root, IMDF_BUNDLE_TTL_HOURS=24)
    monkeypatch.setattr(imdf_router, "settings", fake_settings)
    meta = {
        "project_id": "proj-shape",
        "site_id": "SITE123",
        "saved_at": "2026-04-15T10:00:00",
        "building_count": 2,
        "level_count": 3,
        "unit_count": 5,
        "has_gdb": True,
        "has_ifc": True,
    }
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "get_meta_only",
        lambda task_id: meta if task_id == "task-shape" else None,
    )
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "read_manifest",
        lambda task_id: {"id": "SITE123", "version": "1.0"},
    )
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "read_validation",
        lambda task_id: {"ok": True, "errors": []},
    )
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "read_renderers",
        lambda task_id: {"layers": ["unit"]},
    )
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "list_bundle_file_entries",
        lambda task_id: [
            {"path": "manifest.json", "size": 1},
            {"path": "SITE123/building.geojson", "size": 1},
            {"path": "SITE123/level.geojson", "size": 1},
            {"path": "SITE123/unit.geojson", "size": 1},
        ],
    )
    monkeypatch.setattr(
        imdf_router._imdf_storage,
        "read_geojson",
        lambda task_id, rel: {
            "type": "FeatureCollection",
            "features": [{"type": "Feature"}],
        },
    )
    return root


def test_list_bundles_response_has_expected_shape(imdf_bundles_dir: Path):
    _ = imdf_bundles_dir
    client = _build_client()

    response = client.get("/api/imdf/bundles")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 1
    first = body[0]
    assert set(first.keys()) >= {
        "task_id",
        "project_id",
        "site_id",
        "has_gdb",
        "size_mb",
        "age_hours",
        "saved_at",
        "validation_summary",
        "building_count",
        "level_count",
        "unit_count",
    }
    assert first["task_id"] == "task-shape"
    assert first["project_id"] == "proj-shape"
    assert first["site_id"] == "SITE123"
    assert first["has_gdb"] is True
    assert first["building_count"] == 2
    assert first["level_count"] == 3
    assert first["unit_count"] == 5


def test_get_bundle_metadata_response_has_expected_shape(imdf_bundles_dir: Path):
    _ = imdf_bundles_dir
    client = _build_client()

    response = client.get("/api/imdf/bundles/task-shape")

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "site_id",
        "bundle_task_id",
        "manifest",
        "validation",
        "renderers",
        "geojson",
        "files",
        "has_gdb",
        "has_ifc",
        "has_renderers",
    }
    assert body["bundle_task_id"] == "task-shape"
    assert body["site_id"] == "SITE123"
    assert body["manifest"]["id"] == "SITE123"
    assert body["validation"]["ok"] is True
    assert body["has_gdb"] is True
    assert body["has_ifc"] is True
    assert body["has_renderers"] is True
    assert set(body["geojson"].keys()) == {"building", "level", "unit"}
    assert body["geojson"]["building"]["type"] == "FeatureCollection"
    assert isinstance(body["files"], list)
    assert any(item["path"] == "manifest.json" for item in body["files"])
    assert any(item["path"] == "SITE123/unit.geojson" for item in body["files"])
