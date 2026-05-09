"""
Contract test D3 commit 3 — site_id single-source (loader autoritativo).

Invariant architetturale (d3_imdf_boundaries.md §3.1 + §4.1 + R3 + R4):
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Pre-import per evitare rotture da monkeypatch di settings.
importlib.import_module("app.utils.logger")
imdf_router = importlib.import_module("app.core.imdf.router")

from app.shared.contracts.auth import CurrentUser

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def superadmin() -> CurrentUser:
    return CurrentUser(user_id="u-sa", username="super", group_id="g-sa", role="superadmin")


@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    root = tmp_path / "imdf_bundles"
    root.mkdir()
    import app.config as config_mod
    settings = SimpleNamespace(IMDF_BUNDLES_DIR=root, IMDF_BUNDLE_TTL_HOURS=24)
    monkeypatch.setattr(config_mod, "settings", settings)
    monkeypatch.setattr(imdf_router, "settings", settings)
    return root


def _write_meta(bundle_dir: Path, site_id, project_id: str = "proj-1", **extra):
    """Scrive plenora_metadata.json con `site_id` arbitrario (None/""/str)."""
    bundle_dir.mkdir(parents=True, exist_ok=True)
    meta = {"project_id": project_id, **extra}
    if site_id is not None:
        meta["site_id"] = site_id
    (bundle_dir / "plenora_metadata.json").write_text(
        json.dumps(meta), encoding="utf-8"
    )


# ── 1. get_bundle_metadata: legge site_id da meta (non da scan) ─────────


def test_get_bundle_metadata_reads_site_id_from_meta(fake_settings, superadmin, monkeypatch):
    """Router ritorna site_id dal meta anche se la sottodir sul filesystem
    e' rinominata diversamente: la fonte autoritativa e' meta, non scan."""
    bundle_dir = fake_settings / "task-meta-authoritative"
    _write_meta(bundle_dir, site_id="LO2244-FROM-META")
    # Sottodir sul disco con nome DIVERSO dal meta: prima della fix, la scan
    # avrebbe restituito "DIFFERENT_SITE"; ora deve restare "LO2244-FROM-META".
    (bundle_dir / "DIFFERENT_SITE").mkdir()
    (bundle_dir / "DIFFERENT_SITE" / "manifest.json").write_text("{}", encoding="utf-8")

    # Mock _hydrate_local_cache (commit 2) perche' non vogliamo side effect S3
    import app.core.imdf.storage as storage_mod
    monkeypatch.setattr(storage_mod, "ensure_local_bundle", MagicMock(return_value=bundle_dir))

    result = imdf_router.get_bundle_metadata("task-meta-authoritative", current_user=superadmin)

    assert result["site_id"] == "LO2244-FROM-META"


def test_get_bundle_metadata_returns_null_site_id_when_meta_missing_field(
    fake_settings, superadmin, monkeypatch
):
    """Meta presente ma senza chiave `site_id` (bundle malformato/legacy):
    response site_id=None. Nessun fallback iterdir che inferisca dal nome
    della sottodir."""
    bundle_dir = fake_settings / "task-no-site-id"
    _write_meta(bundle_dir, site_id=None)  # Meta senza site_id
    # Sottodir candidata che pre-fix sarebbe stata rilevata come site_id
    (bundle_dir / "SCAN_CANDIDATE").mkdir()
    (bundle_dir / "SCAN_CANDIDATE" / "manifest.json").write_text("{}", encoding="utf-8")

    import app.core.imdf.storage as storage_mod
    monkeypatch.setattr(storage_mod, "ensure_local_bundle", MagicMock(return_value=bundle_dir))

    result = imdf_router.get_bundle_metadata("task-no-site-id", current_user=superadmin)

    assert result["site_id"] is None


# ── 2. list_bundles: usa solo meta per site_id ──────────────────────────


def test_list_bundles_uses_only_meta_for_site_id(fake_settings, superadmin, monkeypatch):
    """list_bundles legge `meta.get('site_id')` senza fallback iterdir.
    Bundle con meta senza site_id + sottodir candidata -> site_id=None nella
    response ImdfBundleSummary."""
    # Bundle A: site_id presente in meta.
    ba = fake_settings / "task-a"
    _write_meta(ba, site_id="SITE_A")

    # Bundle B: meta SENZA site_id + sottodir candidata sul disco.
    bb = fake_settings / "task-b"
    _write_meta(bb, site_id=None)
    (bb / "LEGACY_SCAN").mkdir()
    (bb / "LEGACY_SCAN" / "manifest.json").write_text("{}", encoding="utf-8")

    # Disabilita lookup S3 in list_bundles per isolare test al disco locale.
    import app.core.imdf.storage as storage_mod
    monkeypatch.setattr(storage_mod, "list_task_ids", lambda: [])

    result = imdf_router.list_bundles(project_id=None, current_user=superadmin)

    by_tid = {b.task_id: b for b in result}
    assert by_tid["task-a"].site_id == "SITE_A"
    # Bundle B: meta non ha site_id, fallback iterdir rimosso -> None.
    assert by_tid["task-b"].site_id is None


# ── 3. R4: loader invariant — ImdfBundle.site_id mai None ───────────────


def test_loader_site_id_invariant_never_none(tmp_path):
    """R4: `ImdfBundle.site_id` popolato da `_build_bundle_index` e' sempre
    una stringa (mai None). Stringa vuota ammessa solo se euristica fallisce
    (bundle malformato senza .gdb ne' manifest ne' geojson).
    """
    from app.core.imdf.loader import _build_bundle_index, ImdfBundle

    # Caso A: bundle con solo .gdb -> site_id derivato dal prefisso gdb
    members_gdb = ["LO9999.gdb/a00000001.gdbtable"]
    (tmp_path / "LO9999.gdb").mkdir()
    bundle_a: ImdfBundle = _build_bundle_index(tmp_path, members_gdb)
    assert bundle_a.site_id == "LO9999"
    assert isinstance(bundle_a.site_id, str)  # mai None

    # Caso B: bundle SOLO con manifest (sites dict esplicito)
    tmp_b = tmp_path / "bundle_b"
    tmp_b.mkdir()
    (tmp_b / "manifest.json").write_text(
        json.dumps({"sites": {"MANIFEST_SITE": {}}}),
        encoding="utf-8",
    )
    bundle_b: ImdfBundle = _build_bundle_index(tmp_b, ["manifest.json"])
    assert bundle_b.site_id == "MANIFEST_SITE"
    assert isinstance(bundle_b.site_id, str)


# ── 4. Single-source: persist scrive bundle.site_id byte-identico ───────


def test_persist_writes_loader_site_id_unchanged(tmp_path, monkeypatch):
    """Persist scrive `meta["site_id"] = bundle.site_id` senza riderivazione.
    Il router (fixtures 1-3) leggera' poi quel valore come autoritativo.
    Confirma single-source enforcement end-to-end (loader -> persist -> meta).
    """
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    # Mock storage.save_bundle per isolare dal path S3 (commit 4 lo sposta,
    # ma oggi persist lo chiama inline).
    import app.core.imdf.storage as storage_mod
    monkeypatch.setattr(storage_mod, "save_bundle", MagicMock())

    # Bundle fake con site_id gia' popolato dal loader fittizio.
    fake_bundle_tmp = tmp_path / "fake_bundle_tmp"
    fake_bundle_tmp.mkdir()
    # Aggiunge un file qualsiasi per il move (persist itera bundle.tmp_dir).
    (fake_bundle_tmp / "placeholder.txt").write_text("x", encoding="utf-8")

    fake_bundle = SimpleNamespace(
        tmp_dir=fake_bundle_tmp,
        site_id="LO7777-UNIQUE",  # valore autoritativo dal loader
        gdb_path=None,
        ifc_path=None,
        renderers_path=None,
    )

    dest_root = tmp_path / "dest" / "task-single-source"
    result = persist_imdf_bundle_to_disk(
        fake_bundle,
        task_id="task-single-source",
        project_id="proj-1",
        dest_root=dest_root,
    )

    # 1. Dict meta ritornato ha site_id byte-identico
    assert result["meta"]["site_id"] == "LO7777-UNIQUE"

    # 2. Il file plenora_metadata.json su disco ha site_id byte-identico
    meta_path = dest_root / "plenora_metadata.json"
    on_disk = json.loads(meta_path.read_text(encoding="utf-8"))
    assert on_disk["site_id"] == "LO7777-UNIQUE"
