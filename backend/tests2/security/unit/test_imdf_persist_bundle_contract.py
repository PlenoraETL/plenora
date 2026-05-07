"""
test_imdf_persist_bundle_contract.py

Contract test per la persistenza di un ImdfBundle su disco. La logica
"""
import json
import shutil
from pathlib import Path

import pytest

import app.utils.logger  # noqa: F401
import app.core.imdf.loader  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract, pytest.mark.regression]


def _fake_bundle(tmp_path: Path, site_id: str = "LO2244") -> "ImdfBundle":
    """Crea una finta tmp dir estratta con struttura IMDF minima + ritorna
    un ImdfBundle vero."""
    from app.core.imdf.loader import ImdfBundle

    src_tmp = tmp_path / f"imdf_{site_id}"
    src_tmp.mkdir()
    # Layout IMDF minimo
    gdb = src_tmp / f"{site_id}.gdb"
    gdb.mkdir()
    (gdb / "a00000001.gdbtable").write_bytes(b"\x00fake gdb\x00")
    site_dir = src_tmp / site_id
    site_dir.mkdir()
    (site_dir / "building.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature"}]}),
        encoding="utf-8",
    )
    (site_dir / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    (src_tmp / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    (src_tmp / "validation.json").write_text(
        json.dumps({"passed": True, "total_errors": 0, "total_warnings": 0}),
        encoding="utf-8",
    )

    bundle = ImdfBundle(
        tmp_dir=src_tmp,
        site_id=site_id,
        gdb_path=gdb,
        manifest_path=src_tmp / "manifest.json",
        validation_path=src_tmp / "validation.json",
    )
    return bundle


@pytest.fixture
def dest_dir(tmp_path, monkeypatch):
    """Patcha settings.IMDF_BUNDLES_DIR a una tmp dir."""
    from types import SimpleNamespace
    import app.config as config_mod
    d = tmp_path / "imdf_bundles"
    d.mkdir()
    fake_settings = SimpleNamespace(
        IMDF_BUNDLES_DIR=d,
        IMDF_BUNDLE_TTL_HOURS=48,
    )
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    return d


# ── 1. Happy path ───────────────────────────────────────────────────
def test_persist_happy_path_moves_files_and_writes_metadata(tmp_path, dest_dir):
    from app.core.imdf.persist import persist_imdf_bundle_to_disk
    bundle = _fake_bundle(tmp_path)
    src_tmp = bundle.tmp_dir
    assert src_tmp.exists()

    result = persist_imdf_bundle_to_disk(
        bundle, task_id="task-abc", project_id="proj-1",
    )

    dest_root = dest_dir / "task-abc"
    assert dest_root.exists() and dest_root.is_dir()
    # File moved to destination
    assert (dest_root / "LO2244.gdb" / "a00000001.gdbtable").exists()
    assert (dest_root / "LO2244" / "building.geojson").exists()
    assert (dest_root / "manifest.json").exists()
    assert (dest_root / "validation.json").exists()
    # Metadata written
    meta_path = dest_root / "plenora_metadata.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["project_id"] == "proj-1"
    assert meta["task_id"] == "task-abc"
    assert meta["site_id"] == "LO2244"
    assert meta["has_gdb"] is True
    # Tmp dir rimossa
    assert not src_tmp.exists()
    # Return contiene il path
    assert result["path"] == str(dest_root)


# ── 2. Move error → tmp dir cleaned ─────────────────────────────────
def test_persist_cleans_tmp_on_move_error(tmp_path, dest_dir, monkeypatch):
    from app.core.imdf import persist as persist_mod
    bundle = _fake_bundle(tmp_path)
    src_tmp = bundle.tmp_dir

    # Forza shutil.move a raise dopo il primo file
    original_move = shutil.move
    state = {"count": 0}
    def flaky_move(src, dst, *args, **kwargs):
        state["count"] += 1
        if state["count"] >= 2:
            raise OSError("simulated move failure")
        return original_move(src, dst, *args, **kwargs)

    monkeypatch.setattr(persist_mod.shutil, "move", flaky_move)

    with pytest.raises(OSError, match="simulated move failure"):
        persist_mod.persist_imdf_bundle_to_disk(
            bundle, task_id="task-err", project_id="proj-1",
        )

    # La tmp dir deve essere cleaned anche su errore
    assert not src_tmp.exists(), (
        f"bundle.tmp_dir non pulita dopo errore di move: {src_tmp}"
    )


# ── 3. Destination esistente viene sovrascritta ─────────────────────
def test_persist_replaces_existing_destination(tmp_path, dest_dir):
    from app.core.imdf.persist import persist_imdf_bundle_to_disk
    # Pre-crea il target con contenuto vecchio
    existing = dest_dir / "task-replace"
    existing.mkdir()
    (existing / "old.txt").write_text("stale", encoding="utf-8")

    bundle = _fake_bundle(tmp_path)
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-replace", project_id="proj-1",
    )

    dest_root = dest_dir / "task-replace"
    assert not (dest_root / "old.txt").exists(), "contenuto stale non rimosso"
    assert (dest_root / "manifest.json").exists(), "nuovo bundle non scritto"


# ── 4. validation_summary incluso nel metadata ──────────────────────
def test_persist_metadata_includes_validation_summary(tmp_path, dest_dir):
    from app.core.imdf.persist import persist_imdf_bundle_to_disk
    bundle = _fake_bundle(tmp_path)
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-val", project_id="proj-1",
    )
    meta = json.loads(
        (dest_dir / "task-val" / "plenora_metadata.json").read_text(encoding="utf-8")
    )
    vs = meta.get("validation_summary")
    assert vs is not None
    assert vs.get("passed") is True
    assert vs.get("total_errors") == 0


# ── 5. Counts layer inclusi nel metadata (per list endpoint) ────────
def test_persist_metadata_includes_layer_counts(tmp_path, dest_dir):
    from app.core.imdf.persist import persist_imdf_bundle_to_disk
    bundle = _fake_bundle(tmp_path)
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-counts", project_id="proj-1",
    )
    meta = json.loads(
        (dest_dir / "task-counts" / "plenora_metadata.json").read_text(encoding="utf-8")
    )
    # Il nostro _fake_bundle ha 1 feature in building.geojson
    assert meta.get("building_count") == 1
