"""Contract test D3 commit 5 — schema plenora_metadata.json + invariant §9.6.

Dichiara lo schema di `plenora_metadata.json` come contratto stabile e
verifica l'invariant "meta sempre presente nel dest dir":
- Persist scrive i campi required + opzionali documentati in
  persist.py docstring.
- Se il write meta fallisce, persist esegue rollback della dest dir
  (shutil.rmtree) e solleva; il worker cattura senza bloccare il task.
  Pre-fix (§9.6): warning + continue -> dir orfana senza meta,
  inaccessibile per ACL fail-closed.

Inoltre verifica la riscrittura di app/core/imdf/__init__.py: rimuove
`processor` fantasma, menziona i moduli reali + D3.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import app.utils.logger  # noqa: F401
import app.core.imdf.loader  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# ── Helpers ─────────────────────────────────────────────────────────────


def _fake_bundle(tmp_path: Path, site_id: str = "LO2244", *, with_geojson: bool = True):
    """Finto ImdfBundle con struttura minima.

    Se `with_geojson=True`, include building/level/unit geojson nella
    sottodir {site_id}/ -> counts opzionali verranno calcolati.
    Se False, solo manifest/validation/gdb -> counts assenti.
    """
    from app.core.imdf.loader import ImdfBundle

    src_tmp = tmp_path / f"imdf_{site_id}"
    src_tmp.mkdir()
    gdb = src_tmp / f"{site_id}.gdb"
    gdb.mkdir()
    (gdb / "a00000001.gdbtable").write_bytes(b"\x00fake gdb\x00")

    (src_tmp / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    (src_tmp / "validation.json").write_text(
        json.dumps({"passed": True, "total_errors": 0, "total_warnings": 2}),
        encoding="utf-8",
    )

    if with_geojson:
        site_dir = src_tmp / site_id
        site_dir.mkdir()
        (site_dir / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
        (site_dir / "building.geojson").write_text(
            json.dumps({"type": "FeatureCollection",
                        "features": [{"type": "Feature"}, {"type": "Feature"}]}),
            encoding="utf-8",
        )
        (site_dir / "level.geojson").write_text(
            json.dumps({"type": "FeatureCollection",
                        "features": [{"type": "Feature"}] * 5}),
            encoding="utf-8",
        )
        (site_dir / "unit.geojson").write_text(
            json.dumps({"type": "FeatureCollection",
                        "features": [{"type": "Feature"}] * 12}),
            encoding="utf-8",
        )

    return ImdfBundle(
        tmp_dir=src_tmp,
        site_id=site_id,
        gdb_path=gdb,
        manifest_path=src_tmp / "manifest.json",
        validation_path=src_tmp / "validation.json",
    )


@pytest.fixture
def dest_dir(tmp_path, monkeypatch):
    import app.config as config_mod
    d = tmp_path / "imdf_bundles"
    d.mkdir()
    fake_settings = SimpleNamespace(IMDF_BUNDLES_DIR=d, IMDF_BUNDLE_TTL_HOURS=48)
    monkeypatch.setattr(config_mod, "settings", fake_settings)
    return d


# ── 1. Schema required fields ──────────────────────────────────────────


def test_plenora_metadata_contains_required_fields(tmp_path, dest_dir):
    """Contract: tutti i campi sempre-presenti dichiarati nel docstring
    di persist.py devono essere scritti nel meta su disco."""
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    bundle = _fake_bundle(tmp_path, site_id="LO2244")
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-schema", project_id="proj-1",
    )

    meta_path = dest_dir / "task-schema" / "plenora_metadata.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Campi sempre presenti (schema persist.py)
    required = {
        "project_id", "task_id", "site_id", "saved_at",
        "has_gdb", "has_ifc", "has_renderers",
    }
    missing = required - meta.keys()
    assert not missing, f"Campi required mancanti nel meta: {missing}"

    # Tipi conformi
    assert meta["project_id"] == "proj-1"
    assert meta["task_id"] == "task-schema"
    assert meta["site_id"] == "LO2244"
    assert isinstance(meta["saved_at"], str) and "T" in meta["saved_at"]
    assert meta["has_gdb"] is True
    assert meta["has_ifc"] is False  # _fake_bundle senza ifc_path
    assert meta["has_renderers"] is False


# ── 2. Schema optional counts present when geojson exists ───────────────


def test_plenora_metadata_contains_optional_counts_when_geojson_present(
    tmp_path, dest_dir,
):
    """Contract: building/level/unit_count presenti se i geojson sono
    nel bundle sotto {site_id}/. Schema persist.py docstring."""
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    bundle = _fake_bundle(tmp_path, site_id="LO9001", with_geojson=True)
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-counts", project_id="proj-1",
    )

    meta = json.loads(
        (dest_dir / "task-counts" / "plenora_metadata.json").read_text(encoding="utf-8")
    )
    # Counts derivati dai geojson del fake bundle
    assert meta.get("building_count") == 2
    assert meta.get("level_count") == 5
    assert meta.get("unit_count") == 12

    # validation_summary presente (validation.json leggibile nel fake)
    assert "validation_summary" in meta
    vs = meta["validation_summary"]
    assert vs.get("passed") is True
    assert vs.get("total_warnings") == 2


def test_plenora_metadata_counts_absent_when_no_geojson(tmp_path, dest_dir):
    """Contract: counts opzionali assenti se i geojson non ci sono nel bundle.
    Assenti, non None: coerente con schema 'optional'."""
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    bundle = _fake_bundle(tmp_path, site_id="LO7777", with_geojson=False)
    persist_imdf_bundle_to_disk(
        bundle, task_id="task-nocounts", project_id="proj-1",
    )

    meta = json.loads(
        (dest_dir / "task-nocounts" / "plenora_metadata.json").read_text(encoding="utf-8")
    )
    assert "building_count" not in meta
    assert "level_count" not in meta
    assert "unit_count" not in meta


# ── 3. Invariant §9.6: meta write failure -> rollback dest + raise ──────


def test_meta_write_failure_cleans_up_dest_and_raises(tmp_path, dest_dir, monkeypatch):
    """Fix D3 §9.6: se `plenora_metadata.json` non riesce ad essere scritto,
    persist esegue rollback della dest dir e solleva. Pre-fix: warning +
    continue lasciava la dir orfana inaccessibile per ACL fail-closed.

    Simuliamo il failure con monkeypatch di Path.write_text: solleva
    OSError quando il path target e' `plenora_metadata.json`.
    """
    from app.core.imdf.persist import persist_imdf_bundle_to_disk

    bundle = _fake_bundle(tmp_path, site_id="LO5555")

    # Intercetta write_text sul path plenora_metadata.json
    real_write_text = Path.write_text

    def fail_on_meta(self, *args, **kwargs):
        if self.name == "plenora_metadata.json":
            raise OSError("simulated disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_on_meta)

    dest_path = dest_dir / "task-fail-meta"
    # Pre: dest dir non esiste
    assert not dest_path.exists()

    # Act: persist solleva (post-fix §9.6)
    with pytest.raises(OSError, match="simulated disk full"):
        persist_imdf_bundle_to_disk(
            bundle, task_id="task-fail-meta", project_id="proj-1",
        )

    # Post-fix: la dest dir NON esiste piu' (rollback via shutil.rmtree)
    assert not dest_path.exists(), (
        "§9.6: la dest dir deve essere rimossa quando il write meta "
        "fallisce. Una dir orfana senza meta e' inaccessibile per ACL "
        "(router._assert_bundle_group_access fail-closed 403) e viola "
        "l'invariant 'meta sempre presente se dir esiste'."
    )

    # bundle.tmp_dir comunque cancellata dal finally di persist
    assert not bundle.tmp_dir.exists(), (
        "Il finally cleanup tmp_dir deve essere eseguito anche su raise "
        "del meta write (pattern try/finally esistente, invariato)."
    )


# ── 4. __init__.py static: no phantom modules, mentions real modules + D3 ─


def test_init_module_docstring_mentions_real_modules_and_d3():
    """Regression: __init__.py cita i moduli reali e D3 come boundary-of-truth.
    Pre-commit (D3 commit 5): citava `processor` fantasma (Fase 1C TODO) e
    `arcgis_upload` (nome scorretto, il modulo reale e' `arcgis_uploader`)."""
    from app.core.imdf import __init__ as imdf_init  # noqa
    import app.core.imdf as imdf_pkg

    doc = imdf_pkg.__doc__ or ""
    for mod in ("loader", "persist", "storage", "router", "validator", "arcgis_uploader"):
        assert mod in doc, f"__init__.py docstring non menziona modulo reale '{mod}'"

    # Menziona D3 come boundary-of-truth
    assert "D3" in doc, "__init__.py docstring non cita D3 come ratifica boundary"


def test_init_module_docstring_does_not_mention_phantom_processor():
    """Regression guard: `processor` era un modulo dichiarato come TODO (Fase
    1C) ma mai esistito. D3 commit 5 lo rimuove dalla docstring.

    Check esatto: non deve apparire la stringa `processor` (anche come
    regressione tipografica).
    """
    import app.core.imdf as imdf_pkg

    doc = imdf_pkg.__doc__ or ""
    assert "processor" not in doc.lower(), (
        "Regression D3 commit 5: modulo fantasma `processor` re-introdotto "
        "in app/core/imdf/__init__.py. Non e' un modulo reale (era Fase 1C "
        "TODO mai realizzata)."
    )
