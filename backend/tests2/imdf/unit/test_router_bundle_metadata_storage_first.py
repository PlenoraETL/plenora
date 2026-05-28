"""
Contract test — GET /imdf/bundles/{task_id} storage-first (IMDF-4).

Copre il routing della response da object storage senza idratare la
cache locale. Le funzioni storage `read_manifest` / `read_validation` /
`read_renderers` / `read_geojson` / `get_meta_only` /
`list_bundle_file_entries` vengono monkeypatchate; ``ensure_local_bundle``
NON deve essere invocata sul path normale.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


# Sentinel meta che soddisfa il check di ownership (project_id valido,
# project visibile dal current_user).
_FAKE_META = {
    "project_id": "proj-1",
    "task_id": "task-x",
    "site_id": "SITE_X",
    "has_gdb": True,
    "has_ifc": False,
    "has_renderers": True,
    "saved_at": "2026-05-25T10:00:00Z",
    "validation_summary": {
        "passed": True,
        "total_errors": 0,
        "total_warnings": 0,
    },
    "building_count": 5,
    "level_count": 2,
    "unit_count": 12,
}


def _bypass_acl(monkeypatch):
    """Monkeypatch del check di ACL: il project esiste e l'utente lo vede."""
    from app.core.imdf import bundle_access as ba

    monkeypatch.setattr(
        ba.project_access, "get_project_for_bundle",
        lambda pid: SimpleNamespace(id=pid, group_id="grp-1"),
    )
    monkeypatch.setattr(
        ba, "assert_resource_visible",
        lambda *a, **k: None,  # no-op: visibile
    )


def _fake_current_user():
    return SimpleNamespace(
        username="alice",
        role="user",
        group_id="grp-1",
        id="user-1",
    )


def test_get_bundle_metadata_uses_storage_readers_no_local_hydration(monkeypatch):
    """Happy path IMDF-4: l'endpoint legge meta + manifest + validation +
    renderers + geojson direct-from-storage. `ensure_local_bundle` /
    `_hydrate_local_cache` NON viene chiamato sul path normale."""
    from app.core.imdf import router as router_mod
    from app.core.imdf import storage as st_mod

    # Storage readers mockati.
    monkeypatch.setattr(st_mod, "get_meta_only", lambda tid: _FAKE_META)
    monkeypatch.setattr(
        st_mod, "read_manifest",
        lambda tid: {"sites": {"SITE_X": {"name": "Site X"}}},
    )
    monkeypatch.setattr(
        st_mod, "read_validation",
        lambda tid: {"passed": True, "total_errors": 0},
    )
    monkeypatch.setattr(
        st_mod, "read_renderers",
        lambda tid: {"layers": [{"name": "Localita"}]},
    )
    monkeypatch.setattr(
        st_mod, "list_bundle_file_entries",
        lambda tid: [
            {"path": "manifest.json", "size": 100},
            {"path": "SITE_X/building.geojson", "size": 1500},
        ],
    )
    monkeypatch.setattr(
        st_mod, "read_geojson",
        lambda tid, rel: {"type": "FeatureCollection", "features": []},
    )

    # Post QUERY-1 step 3: `_hydrate_local_cache` rimosso. Sentinel
    # inline che il symbol non esiste e' coperto dal test
    # `test_validate_publish_hydrate_legacy_tombstoned`.
    assert not hasattr(router_mod, "_hydrate_local_cache")

    _bypass_acl(monkeypatch)

    user = _fake_current_user()
    out = router_mod.get_bundle_metadata("task-x", current_user=user)

    # Storage-first response shape.
    assert out["bundle_task_id"] == "task-x"
    assert out["site_id"] == "SITE_X"
    assert out["manifest"] == {"sites": {"SITE_X": {"name": "Site X"}}}
    assert out["validation"] == {"passed": True, "total_errors": 0}
    assert out["renderers"] == {"layers": [{"name": "Localita"}]}
    assert "building" in out["geojson"]  # geojson stem indexed
    # Flags derivati dal meta, NON da scan filesystem.
    assert out["has_gdb"] is True
    assert out["has_ifc"] is False
    assert out["has_renderers"] is True


def test_get_bundle_metadata_404_when_not_on_storage(monkeypatch):
    """Bundle assente su storage -> 404."""
    from app.core.imdf import router as router_mod
    from app.core.imdf import storage as st_mod

    monkeypatch.setattr(st_mod, "get_meta_only", lambda tid: None)
    # Post-tombstone: _hydrate_local_cache rimosso; no patch needed.

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        router_mod.get_bundle_metadata("task-missing", current_user=_fake_current_user())
    assert exc_info.value.status_code == 404
    detail = exc_info.value.detail
    assert isinstance(detail, dict)
    assert "non trovato" in detail.get("message", "")


def test_get_bundle_metadata_forbids_cross_tenant_via_meta_acl(monkeypatch):
    """Group ownership check usa il meta da storage: bundle di un
    progetto non visibile all'utente -> 403/404 dal visible_checker."""
    from app.core.imdf import router as router_mod
    from app.core.imdf import storage as st_mod
    from app.core.imdf import bundle_access as ba
    from fastapi import HTTPException

    monkeypatch.setattr(st_mod, "get_meta_only", lambda tid: _FAKE_META)
    monkeypatch.setattr(st_mod, "read_manifest", lambda tid: None)
    monkeypatch.setattr(st_mod, "read_validation", lambda tid: None)
    monkeypatch.setattr(st_mod, "read_renderers", lambda tid: None)
    monkeypatch.setattr(st_mod, "list_bundle_file_entries", lambda tid: [])
    # Post-tombstone: _hydrate_local_cache rimosso; no patch needed.

    # Project esiste ma il visible_checker rifiuta (cross-tenant).
    monkeypatch.setattr(
        ba.project_access, "get_project_for_bundle",
        lambda pid: SimpleNamespace(id=pid, group_id="grp-other"),
    )
    def _deny(*a, **k):
        raise HTTPException(status_code=403, detail="Accesso negato")
    monkeypatch.setattr(ba, "assert_resource_visible", _deny)

    with pytest.raises(HTTPException) as exc_info:
        router_mod.get_bundle_metadata("task-x", current_user=_fake_current_user())
    assert exc_info.value.status_code == 403


def test_get_bundle_metadata_legacy_fallback_removed_post_tombstone(monkeypatch):
    """IMDF-4 step 4 tombstone: il fallback legacy (cache locale +
    `bundle_dir.rglob`) e' rimosso. Bundle pre-IMDF-3 senza meta su
    storage -> 404 BUNDLE_NOT_FOUND. Richiede re-upload via
    `persist_imdf_bundle` per essere accessibili."""
    from app.core.imdf import router as router_mod
    from app.core.imdf import storage as st_mod
    from fastapi import HTTPException

    monkeypatch.setattr(st_mod, "get_meta_only", lambda tid: None)
    monkeypatch.setattr(st_mod, "read_manifest", lambda tid: None)
    monkeypatch.setattr(st_mod, "read_validation", lambda tid: None)
    monkeypatch.setattr(st_mod, "read_renderers", lambda tid: None)
    monkeypatch.setattr(st_mod, "list_bundle_file_entries", lambda tid: [])

    with pytest.raises(HTTPException) as exc_info:
        router_mod.get_bundle_metadata(
            "task-legacy", current_user=_fake_current_user(),
        )
    assert exc_info.value.status_code == 404


def test_storage_read_helpers_use_read_bundle_file_bytes(monkeypatch):
    """Pin del wiring interno: read_manifest/validation/renderers/geojson
    chiamano `read_bundle_file_bytes`. Garantisce che gli helper restino
    `app/core/imdf/storage.py`-internal (no detour via filesystem).
    """
    from app.core.imdf import storage as st_mod

    captured = []

    def _fake(task_id, name):
        captured.append((task_id, name))
        return json.dumps({"k": name}).encode("utf-8")

    monkeypatch.setattr(st_mod, "read_bundle_file_bytes", _fake)

    assert st_mod.read_manifest("t") == {"k": "manifest.json"}
    assert st_mod.read_validation("t") == {"k": "validation.json"}
    assert st_mod.read_renderers("t") == {"k": "renderers.json"}
    assert st_mod.read_geojson("t", "S/foo.geojson") == {"k": "S/foo.geojson"}

    assert captured == [
        ("t", "manifest.json"),
        ("t", "validation.json"),
        ("t", "renderers.json"),
        ("t", "S/foo.geojson"),
    ]


def test_read_geojson_rejects_non_geojson_extension(monkeypatch):
    """Defensive: read_geojson rifiuta path con estensione non-geojson,
    senza toccare lo storage. Pin del guard di estensione."""
    from app.core.imdf import storage as st_mod

    monkeypatch.setattr(
        st_mod, "read_bundle_file_bytes",
        lambda tid, name: pytest.fail(
            "read_bundle_file_bytes non deve essere invocato per estensione "
            "non-geojson"
        ),
    )
    assert st_mod.read_geojson("t", "manifest.json") is None
    assert st_mod.read_geojson("t", "S/foo.txt") is None
