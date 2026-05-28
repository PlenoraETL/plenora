"""
Contract test — `extract_imdf_zip_bytes` (IMDF-1, Phase6 step 1).

Copre:
- `extract_imdf_zip_bytes(data)` accetta bytes RAM-only e ritorna un
  `ImdfBundle` indicizzato (manifest/geojson/site_id) senza richiedere
  un file `.zip` su disco al caller.
- Input vuoto -> `ValueError` esplicito (no fall-through silente).
- Zip senza componenti IMDF valide -> `ValueError("non sembra un IMDF...")`
  con cleanup della tmp dir creata internamente.
Il loader espone solo la shape RAM-first: niente path-mode, niente tmp
dir, niente accessor filesystem.
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.imdf, pytest.mark.contract]


def _build_minimal_imdf_zip_bytes() -> bytes:
    """Costruisce uno zip IMDF minimale in-memory.

    Contenuto:
      - manifest.json con sites={"SITE_X": {...}}
      - SITE_X/anchor.geojson (geojson dummy)
    Manca .gdb e .ifc (non sono richiesti per la validazione di
    `_build_bundle_index`: bastano manifest+geojson per essere un IMDF
    "valido per indicizzazione").
    """
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "manifest.json",
            json.dumps({"sites": {"SITE_X": {"name": "Site X"}}}),
        )
        z.writestr(
            "SITE_X/anchor.geojson",
            json.dumps({"type": "FeatureCollection", "features": []}),
        )
    return buf.getvalue()


def test_extract_imdf_zip_bytes_returns_indexed_bundle():
    """Happy path IMDF-6 fully-RAM: bytes IMDF validi → bundle con
    files popolati, tmp_dir=None, *_path=None. Tutti i componenti
    sono RAM-resident (bundle.files / gdb_zip_bytes / ifc_bytes)."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    data = _build_minimal_imdf_zip_bytes()

    bundle = extract_imdf_zip_bytes(data)
    try:
        assert bundle.site_id == "SITE_X"
        # IMDF-6: zero disk. tmp_dir None.
        assert bundle.tmp_dir is None
        # I componenti vivono in bundle.files.
        assert "manifest.json" in bundle.files
        assert "SITE_X/anchor.geojson" in bundle.files
        # raw_filelist riflette l'ordine zip.
        assert "manifest.json" in bundle.raw_filelist
        assert "SITE_X/anchor.geojson" in bundle.raw_filelist
    finally:
        bundle.cleanup()  # no-op safe con tmp_dir=None.


def test_extract_imdf_zip_bytes_empty_input_raises_value_error():
    """`data=b""` -> ValueError esplicito; niente filesystem touch."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    with pytest.raises(ValueError, match="vuoti"):
        extract_imdf_zip_bytes(b"")


def test_extract_imdf_zip_bytes_invalid_zip_no_tmp_created():
    """IMDF-6 + QUERY-1 step 3 tombstone: `loader` non importa piu'
    `tempfile` (path-mode rimosso). Estraendo uno zip senza componenti
    IMDF -> ValueError SENZA toccare filesystem.

    Anti-regression: doppio assert (a) il modulo NON ha `tempfile` come
    attribute (import rimosso); (b) il ValueError viene sollevato senza
    side effect.
    """
    from app.core.imdf import loader as _loader
    assert not hasattr(_loader, "tempfile"), (
        "loader NON deve importare tempfile post-QUERY-1 step 3 "
        "(path-mode rimosso)."
    )

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("random.txt", "noise")
    bogus_data = buf.getvalue()

    with pytest.raises(ValueError, match="non sembra un IMDF"):
        _loader.extract_imdf_zip_bytes(bogus_data)


def test_extract_imdf_zip_bytes_collects_gdb_into_gdb_zip_bytes():
    """IMDF-6: i membri `*.gdb/...` dello zip incoming vengono
    ricomposti in `bundle.gdb_zip_bytes` (sub-zip RAM con stesso
    layout consumato da `open_gdb_in_vsimem`. Pin del contratto
    inter-modulare: `open_gdb_in_vsimem` cerca `{site}.gdb/` come
    top-level dentro `_gdb.zip` su storage."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    # Zip incoming con .gdb members + manifest.
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps({"sites": {"SITE_X": {}}}))
        z.writestr("SITE_X.gdb/a00000001.gdbtable", b"\x00fake gdb body\x00")
        z.writestr("SITE_X.gdb/a00000001.gdbtablx", b"\x00fake gdb idx\x00")
    data = buf.getvalue()

    bundle = extract_imdf_zip_bytes(data)
    assert bundle.gdb_zip_bytes is not None
    assert bundle.has_gdb is True

    # Il sub-zip ha gli stessi arcname dei .gdb members originali.
    sub = zipfile.ZipFile(BytesIO(bundle.gdb_zip_bytes))
    sub_names = set(sub.namelist())
    assert sub_names == {
        "SITE_X.gdb/a00000001.gdbtable",
        "SITE_X.gdb/a00000001.gdbtablx",
    }


def test_extract_imdf_zip_bytes_ifc_into_ifc_bytes():
    """IMDF-6: file `.ifc` raccolto in `bundle.ifc_bytes` direttamente
    (no on-disk path)."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"sites": {"S1": {}}}))
        z.writestr("S1.ifc", b"ISO-10303-21 fake ifc body")
    data = buf.getvalue()

    bundle = extract_imdf_zip_bytes(data)
    assert bundle.ifc_bytes is not None
    assert bundle.ifc_bytes.startswith(b"ISO-10303-21")
    assert bundle.has_ifc is True


def test_extract_imdf_zip_path_mode_tombstoned():
    """Sentinel: `extract_imdf_zip(path)` rimosso post-QUERY-1 step 3.
    Bytes-mode `extract_imdf_zip_bytes(bytes)` e' l'unico entry."""
    from app.core.imdf import loader as loader_mod
    assert not hasattr(loader_mod, "extract_imdf_zip"), (
        "extract_imdf_zip(path) rimosso post-QUERY-1 step 3. "
        "Usa extract_imdf_zip_bytes(bytes)."
    )


# IMDF-2: shape RAM-first (`bundle.files`)


def test_bundle_files_populated_from_zip_members():
    """IMDF-2: `extract_imdf_zip_bytes` popola `bundle.files` con i
    membri RAM-first (manifest, validation, renderers, geojson) PRIMA
    dell'extract su disco. Accessor `manifest()` / `validation()` /
    `renderers()` leggono dai bytes in memoria, niente filesystem read."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "manifest.json",
            json.dumps({"sites": {"S1": {"name": "Site 1"}}}),
        )
        z.writestr(
            "validation.json",
            json.dumps({"passed": True, "total_errors": 0}),
        )
        z.writestr(
            "renderers.json",
            json.dumps({"layers": [{"name": "Localita"}]}),
        )
        z.writestr(
            "S1/anchor.geojson",
            json.dumps({"type": "FeatureCollection", "features": []}),
        )
    data = buf.getvalue()

    bundle = extract_imdf_zip_bytes(data)
    try:
        # Files dict popolato per i membri RAM-first.
        assert "manifest.json" in bundle.files
        assert "validation.json" in bundle.files
        assert "renderers.json" in bundle.files
        assert "S1/anchor.geojson" in bundle.files
        # Accessor che leggono da `files` (no filesystem read).
        assert bundle.manifest() == {"sites": {"S1": {"name": "Site 1"}}}
        assert bundle.validation() == {"passed": True, "total_errors": 0}
        assert bundle.renderers() == {"layers": [{"name": "Localita"}]}
        # Properties has_* derivate dalla shape ibrida.
        assert bundle.has_manifest is True
        assert bundle.has_validation is True
        assert bundle.has_renderers is True
        assert bundle.has_gdb is False  # no .gdb in questo zip
        assert bundle.has_ifc is False  # no .ifc
    finally:
        bundle.cleanup()


def test_bundle_files_accessor_path_fallback_tombstoned():
    """Gli accessor leggono solo da `bundle.files`: i campi path-mode
    sono rimossi dal dataclass."""
    from app.core.imdf.loader import ImdfBundle

    bundle = ImdfBundle(tmp_dir=None, site_id="SITE_X", files={})
    assert not hasattr(bundle, "manifest_path")
    assert not hasattr(bundle, "validation_path")
    assert not hasattr(bundle, "renderers_path")
    assert not hasattr(bundle, "gdb_path")
    assert not hasattr(bundle, "ifc_path")


def test_bundle_manifest_missing_returns_empty_dict():
    """Quando il manifest non e' presente in `files`, gli accessor ritornano
    `{}` senza sollevare. Garantisce che i consumer optional-aware
    (es. metadata builder) possano interrogare il bundle senza
    pre-check."""
    from app.core.imdf.loader import ImdfBundle

    bundle = ImdfBundle(tmp_dir=None, site_id="X", files={})
    assert bundle.manifest() == {}
    assert bundle.validation() == {}
    assert bundle.renderers() == {}
    assert bundle.has_manifest is False
    assert bundle.has_validation is False
    assert bundle.has_renderers is False
    assert bundle.has_gdb is False
    assert bundle.has_ifc is False
    # cleanup e' un no-op safe con tmp_dir=None.
    bundle.cleanup()


def test_bundle_tmp_dir_optional_none_cleanup_no_op():
    """Anti-regression: `cleanup()` con `tmp_dir=None` non solleva.
    Preparazione per IMDF-3 dove bundle puramente in-memory (no .gdb,
    no .ifc) potrebbe non avere tmp_dir."""
    from app.core.imdf.loader import ImdfBundle

    bundle = ImdfBundle(tmp_dir=None, site_id="X")
    bundle.cleanup()  # no raise
