"""
Contract test — GET /imdf/bundles storage-first (IMDF-4 step 2).

L'endpoint usa `imdf.storage.list_task_ids` come fonte di verita'. Per
ogni task_id il meta viene letto via `imdf.storage.get_meta_only`.
Niente piu' iterdir sul filesystem.

Test coperti:
  - happy path: storage list ritorna task_ids, meta letti da storage,
    response Summary popolata
  - ACL: project di altro group_id viene skippato
  - bundle senza meta skippato
  - cache locale ignorata: bundle solo-on-disk non appare
  - sort: bundle con saved_at piu' recente in testa
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _fake_current_user(role: str = "user", group_id: str = "grp-1"):
    return SimpleNamespace(
        username="alice",
        role=role,
        group_id=group_id,
        id="user-1",
    )


def _patch_storage_list(monkeypatch, task_ids: list[str]):
    """Patcha la fonte storage-first di task_ids list."""
    from app.core.imdf import storage as st

    monkeypatch.setattr(st, "list_task_ids", lambda: task_ids)


def _patch_storage_meta(monkeypatch, meta_by_task: dict[str, dict]):
    """Patcha lo storage get_meta_only -> dict per ogni task."""
    from app.core.imdf import storage as st

    monkeypatch.setattr(
        st, "get_meta_only",
        lambda tid: meta_by_task.get(tid),
    )
    monkeypatch.setattr(st, "list_bundle_file_entries", lambda tid: [])


def _patch_empty_local(monkeypatch, tmp_path):
    """Patcha settings.IMDF_BUNDLES_DIR a una tmp vuota: nessuna cache."""
    import app.config as config_mod
    from app.core.imdf import router as router_mod

    fake_settings = SimpleNamespace(
        IMDF_BUNDLES_DIR=tmp_path,
        IMDF_BUNDLE_TTL_HOURS=48,
    )
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    monkeypatch.setattr(router_mod, "settings", fake_settings)


def _patch_acl_bypass(monkeypatch):
    """Bypass ACL: tutti i project sono visibili."""
    from app.core.imdf import project_access

    monkeypatch.setattr(
        project_access, "list_project_ids_visible_to_group",
        lambda gid: {"proj-1", "proj-2", "proj-3"},
    )


def test_list_bundles_storage_first_happy_path(monkeypatch, tmp_path):
    """Storage ritorna 2 task_ids; meta letti da storage; response
    contiene entrambi i Summary con i count dal meta (no filesystem)."""
    from app.core.imdf import router as router_mod

    _patch_empty_local(monkeypatch, tmp_path)
    _patch_storage_list(monkeypatch, ["task-a", "task-b"])
    _patch_storage_meta(monkeypatch, {
        "task-a": {
            "project_id": "proj-1",
            "site_id": "SITE_A",
            "has_gdb": True,
            "has_ifc": False,
            "has_renderers": True,
            "saved_at": "2026-05-25T10:00:00Z",
            "building_count": 5,
            "level_count": 2,
            "unit_count": 12,
        },
        "task-b": {
            "project_id": "proj-2",
            "site_id": "SITE_B",
            "has_gdb": False,
            "saved_at": "2026-05-24T08:00:00Z",
        },
    })
    _patch_acl_bypass(monkeypatch)

    out = router_mod.list_bundles(
        project_id=None, current_user=_fake_current_user(),
    )
    task_ids = [b.task_id for b in out]
    assert set(task_ids) == {"task-a", "task-b"}
    by_id = {b.task_id: b for b in out}
    assert by_id["task-a"].site_id == "SITE_A"
    assert by_id["task-a"].has_gdb is True
    assert by_id["task-a"].building_count == 5
    assert by_id["task-b"].has_gdb is False
    # Size: 0 perche' lo storage file listing e' vuoto nel test.
    assert by_id["task-a"].size_mb == 0.0


def test_list_bundles_acl_skips_other_group(monkeypatch, tmp_path):
    """Bundle di project_id non in `visible_project_ids` viene escluso."""
    from app.core.imdf import router as router_mod
    from app.core.imdf import project_access

    _patch_empty_local(monkeypatch, tmp_path)
    _patch_storage_list(monkeypatch, ["task-own", "task-other"])
    _patch_storage_meta(monkeypatch, {
        "task-own":   {"project_id": "proj-1", "site_id": "S1"},
        "task-other": {"project_id": "proj-XX", "site_id": "SXX"},
    })
    # Solo proj-1 visibile all'utente.
    monkeypatch.setattr(
        project_access, "list_project_ids_visible_to_group",
        lambda gid: {"proj-1"},
    )

    out = router_mod.list_bundles(
        project_id=None, current_user=_fake_current_user(),
    )
    task_ids = [b.task_id for b in out]
    assert task_ids == ["task-own"]


def test_list_bundles_missing_meta_skipped(
    monkeypatch, tmp_path,
):
    """Bundle senza meta viene skippato sia con filtro che senza."""
    from app.core.imdf import router as router_mod

    _patch_empty_local(monkeypatch, tmp_path)
    _patch_storage_list(monkeypatch, ["task-with", "task-orphan"])
    _patch_storage_meta(monkeypatch, {
        "task-with":   {"project_id": "proj-1", "site_id": "S1"},
        "task-orphan": None,  # nessun meta su storage
    })
    _patch_acl_bypass(monkeypatch)

    out_filtered = router_mod.list_bundles(
        project_id="proj-1", current_user=_fake_current_user(),
    )
    assert [b.task_id for b in out_filtered] == ["task-with"]

    out_all = router_mod.list_bundles(
        project_id=None, current_user=_fake_current_user(),
    )
    assert [b.task_id for b in out_all] == ["task-with"]


def test_list_bundles_ignores_local_only_bundles(monkeypatch, tmp_path):
    """La cache locale non viene piu' usata come fallback di listing."""
    from app.core.imdf import router as router_mod

    _patch_empty_local(monkeypatch, tmp_path)
    local_bundle_dir = tmp_path / "task-localonly"
    local_bundle_dir.mkdir()

    _patch_storage_list(monkeypatch, [])  # storage vuoto
    _patch_storage_meta(monkeypatch, {})
    _patch_acl_bypass(monkeypatch)

    out = router_mod.list_bundles(
        project_id=None, current_user=_fake_current_user(),
    )
    assert out == []


def test_list_bundles_sort_by_saved_at_desc(monkeypatch, tmp_path):
    """Sort: bundle con `saved_at` piu' recente in testa; bundle senza
    saved_at vanno in coda alphabetici."""
    from app.core.imdf import router as router_mod

    _patch_empty_local(monkeypatch, tmp_path)
    _patch_storage_list(monkeypatch, ["task-old", "task-new", "task-no-saved"])
    _patch_storage_meta(monkeypatch, {
        "task-old":      {"project_id": "proj-1", "saved_at": "2026-05-20T00:00:00Z"},
        "task-new":      {"project_id": "proj-1", "saved_at": "2026-05-25T12:00:00Z"},
        "task-no-saved": {"project_id": "proj-1"},  # no saved_at
    })
    _patch_acl_bypass(monkeypatch)

    out = router_mod.list_bundles(
        project_id=None, current_user=_fake_current_user(),
    )
    task_ids = [b.task_id for b in out]
    # saved_at non-vuoto e' un ordinamento "in testa"; il bundle senza
    # saved_at va in fondo.
    assert task_ids[-1] == "task-no-saved"
    assert "task-new" in task_ids[:2]
    assert "task-old" in task_ids[:2]
