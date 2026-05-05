"""
test_imdf_bundles_management_contract.py

Contract test per la gestione storico bundle IMDF:
  - GET /api/imdf/bundles arricchito con building_count / level_count /
    unit_count (da plenora_metadata.json o fallback counting lazily i
    geojson dentro il bundle dir).
  - DELETE /api/imdf/bundles/{id} — cancella la cartella del bundle, con
    path-traversal guard e group-access check.
"""
import json
import pytest
from pathlib import Path

# Pre-import per evitare che il monkeypatch di settings rompa init-time reads.
import app.utils.logger  # noqa: F401
import app.core.features.features_service  # noqa: F401
import app.db.db_service  # noqa: F401
import app.core.auth.auth  # noqa: F401
import app.core.imdf.router as imdf_router  # noqa: F401

from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


@pytest.fixture
def superadmin() -> CurrentUser:
    return CurrentUser(user_id="u-sa", username="super", group_id="g-sa", role="superadmin")


@pytest.fixture
def normal_user() -> CurrentUser:
    return CurrentUser(user_id="u-1", username="mario", group_id="g-1", role="admin")


@pytest.fixture
def bundles_dir(tmp_path, monkeypatch):
    """Monkeypatcha settings.IMDF_BUNDLES_DIR a una tmp dir con 2 bundle di
    test (con metadata + manifest.json + geojson finti)."""
    root = tmp_path / "imdf_bundles"
    root.mkdir()

    # Bundle 1: metadata completo + counts espliciti
    b1 = root / "task-aaa"
    b1.mkdir()
    (b1 / "plenora_metadata.json").write_text(json.dumps({
        "project_id": "proj-1",
        "site_id": "LO2244",
        "saved_at": "2026-04-14T10:00:00",
        "building_count": 3,
        "level_count": 12,
        "unit_count": 450,
    }), encoding="utf-8")
    (b1 / "LO2244.gdb").mkdir()

    # Bundle 2: metadata base (senza counts) → fallback counting dai geojson
    b2 = root / "task-bbb"
    b2.mkdir()
    (b2 / "plenora_metadata.json").write_text(json.dumps({
        "project_id": "proj-1",
        "site_id": "LO9001",
        "saved_at": "2026-04-14T11:00:00",
    }), encoding="utf-8")
    site = b2 / "LO9001"
    site.mkdir()
    (site / "building.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature"}],  # 1 building
    }), encoding="utf-8")
    (site / "level.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature"}] * 4,  # 4 levels
    }), encoding="utf-8")
    (site / "unit.geojson").write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{"type": "Feature"}] * 80,  # 80 units
    }), encoding="utf-8")
    (b2 / "LO9001.gdb").mkdir()

    # Patch settings
    from types import SimpleNamespace
    import app.config as config_mod
    monkeypatch.setattr(config_mod, "settings", SimpleNamespace(
        IMDF_BUNDLES_DIR=root,
        IMDF_BUNDLE_TTL_HOURS=24,
    ))
    # Patch nel modulo router (già importato → ha riferimento proprio a settings)
    monkeypatch.setattr(imdf_router, "settings", config_mod.settings)
    return root


# ── 1. LIST arricchito: counts da plenora_metadata.json ────────────
def test_list_bundles_returns_counts_from_metadata(bundles_dir, superadmin):
    result = imdf_router.list_bundles(project_id=None, current_user=superadmin)
    assert len(result) >= 2
    b1 = next(b for b in result if b.task_id == "task-aaa")
    assert b1.building_count == 3
    assert b1.level_count == 12
    assert b1.unit_count == 450


def test_list_bundles_fallback_counts_from_geojson(bundles_dir, superadmin):
    """Se metadata non ha counts, il validator conta lazy dai geojson."""
    result = imdf_router.list_bundles(project_id=None, current_user=superadmin)
    b2 = next(b for b in result if b.task_id == "task-bbb")
    assert b2.building_count == 1
    assert b2.level_count == 4
    assert b2.unit_count == 80


def test_list_bundles_handles_missing_manifest_gracefully(tmp_path, monkeypatch, superadmin):
    """Bundle senza metadata né geojson: counts = None, niente crash."""
    root = tmp_path / "imdf_bundles"
    root.mkdir()
    (root / "task-empty").mkdir()  # vuota
    from types import SimpleNamespace
    import app.config as config_mod
    fake_settings = SimpleNamespace(IMDF_BUNDLES_DIR=root, IMDF_BUNDLE_TTL_HOURS=24)
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    monkeypatch.setattr(imdf_router, "settings", fake_settings)

    result = imdf_router.list_bundles(project_id=None, current_user=superadmin)
    empty = next((b for b in result if b.task_id == "task-empty"), None)
    # Bundle senza plenora_metadata non deve avere counts
    if empty is not None:
        assert empty.building_count is None
        assert empty.level_count is None
        assert empty.unit_count is None


# ── 2. DELETE endpoint ─────────────────────────────────────────────
def test_delete_bundle_removes_directory(bundles_dir, superadmin):
    # superadmin bypass del group-access check → delete success
    before = (bundles_dir / "task-aaa").exists()
    assert before, "fixture preconditions"
    imdf_router.delete_bundle(bundle_task_id="task-aaa", current_user=superadmin)
    assert not (bundles_dir / "task-aaa").exists()


def test_delete_bundle_rejects_invalid_id(bundles_dir, superadmin):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        imdf_router.delete_bundle(bundle_task_id="../../../etc/passwd", current_user=superadmin)
    assert exc.value.status_code == 400


def test_delete_bundle_returns_404_when_missing(bundles_dir, superadmin):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        imdf_router.delete_bundle(bundle_task_id="task-ghost", current_user=superadmin)
    assert exc.value.status_code == 404


def test_delete_bundle_blocks_cross_group(bundles_dir, normal_user, monkeypatch):
    """Il normal_user di gruppo g-1 NON ha project proj-1 visibile → 403."""
    import app.db.db_service as db_svc
    # BE-GO-READY-AUTH-ENGINE-020: patch via consuming router-module
    # surface; IMDF router imports `assert_resource_visible` from the
    # ownership leaf at module top, the facade is no longer the
    # monkeypatch target.
    import app.core.imdf.router as imdf_router_mod
    # Il progetto del bundle esiste, ma NON è visibile all'utente
    monkeypatch.setattr(db_svc, "get_project",
                        lambda pid: {"id": pid, "group_id": "g-OTHER"})

    def _deny(*a, **kw):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Accesso negato")
    monkeypatch.setattr(imdf_router_mod, "assert_resource_visible", _deny)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        imdf_router.delete_bundle(bundle_task_id="task-aaa", current_user=normal_user)
    assert exc.value.status_code == 403
    # La cartella NON deve essere stata rimossa
    assert (bundles_dir / "task-aaa").exists()
