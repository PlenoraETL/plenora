"""
Contract test — `extract_imdf_zip_bytes` (IMDF-1, Phase6 step 1).

Copre:
- `extract_imdf_zip_bytes(data)` accetta bytes RAM-only e ritorna un
  `ImdfBundle` indicizzato (manifest/geojson/site_id) senza richiedere
  un file `.zip` su disco al caller.
- Input vuoto -> `ValueError` esplicito (no fall-through silente).
- Zip senza componenti IMDF valide -> `ValueError("non sembra un IMDF...")`
  con cleanup della tmp dir creata internamente.
- Equivalenza output con `extract_imdf_zip(path)` per la stessa zip:
  i due percorsi (path-based vs bytes-based) devono produrre lo stesso
  set di file indicizzati e lo stesso `site_id`.

NOTA su tmp dir: IMDF-1 chiude solo il bounce nel caller (worker non usa
piu' `temporary_path_from_bytes`). Il loader internamente continua a
estrarre su tmp dir perche' `.gdb` e' un formato directory letto da
pyogrio via filesystem; IMDF-2 introdurra' la shape in-memory
`ImdfBundle.files: dict[str, bytes]`.
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
    """Happy path: bytes IMDF validi → bundle indicizzato con
    manifest, geojson dir, site_id risolto dal manifest."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    data = _build_minimal_imdf_zip_bytes()

    bundle = extract_imdf_zip_bytes(data)
    try:
        assert bundle.site_id == "SITE_X"
        assert bundle.manifest_path is not None
        assert bundle.manifest_path.exists()
        assert bundle.imdf_dir is not None
        assert bundle.imdf_dir.name == "SITE_X"
        assert len(bundle.imdf_geojson_files) == 1
        assert bundle.imdf_geojson_files[0].name == "anchor.geojson"
        # raw_filelist riflette l'ordine zip
        assert "manifest.json" in bundle.raw_filelist
        assert "SITE_X/anchor.geojson" in bundle.raw_filelist
    finally:
        bundle.cleanup()


def test_extract_imdf_zip_bytes_empty_input_raises_value_error():
    """`data=b""` -> ValueError esplicito; niente tmp dir creata."""
    from app.core.imdf.loader import extract_imdf_zip_bytes

    with pytest.raises(ValueError, match="vuoti"):
        extract_imdf_zip_bytes(b"")


def test_extract_imdf_zip_bytes_invalid_zip_cleans_up_tmp(tmp_path, monkeypatch):
    """Zip senza componenti IMDF (no .gdb, no manifest) → ValueError
    + cleanup della tmp dir interna. Anti-regression: senza la
    `shutil.rmtree(tmp, ignore_errors=True)` nel try/except, ogni
    extract fallito lascerebbe una dir `imdf_*` orfana."""
    from app.core.imdf import loader as _loader

    # Forziamo la tmp dir interna sotto `tmp_path` per ispezione.
    tmp_dirs_created: list[Path] = []
    real_mkdtemp = _loader.tempfile.mkdtemp

    def _capturing_mkdtemp(prefix=""):
        tmp = real_mkdtemp(prefix=prefix, dir=str(tmp_path))
        tmp_dirs_created.append(Path(tmp))
        return tmp

    monkeypatch.setattr(_loader.tempfile, "mkdtemp", _capturing_mkdtemp)

    # Zip valido come archivio ma senza manifest ne' .gdb: deve essere
    # rifiutato dal `_build_bundle_index`.
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("random.txt", "noise")
    bogus_data = buf.getvalue()

    with pytest.raises(ValueError, match="non sembra un IMDF"):
        _loader.extract_imdf_zip_bytes(bogus_data)

    assert len(tmp_dirs_created) == 1, (
        "extract_imdf_zip_bytes deve aver creato esattamente 1 tmp dir"
    )
    assert not tmp_dirs_created[0].exists(), (
        "tmp dir dell'extract bytes deve essere ripulita su ValueError "
        "(altrimenti accumulo orfani imdf_* nel worker)"
    )


def test_extract_imdf_zip_bytes_equivalent_to_path_based(tmp_path):
    """Equivalenza output: lo stesso zip aperto da path o da bytes
    deve produrre lo stesso `site_id` + lo stesso set di file
    indicizzati. Anti-regression sul refactor `_extract_zip_source_to_bundle`."""
    from app.core.imdf.loader import extract_imdf_zip, extract_imdf_zip_bytes

    data = _build_minimal_imdf_zip_bytes()
    zip_on_disk = tmp_path / "imdf.zip"
    zip_on_disk.write_bytes(data)

    bundle_from_path = extract_imdf_zip(str(zip_on_disk))
    bundle_from_bytes = extract_imdf_zip_bytes(data)
    try:
        assert bundle_from_path.site_id == bundle_from_bytes.site_id
        assert set(bundle_from_path.raw_filelist) == set(
            bundle_from_bytes.raw_filelist
        )
        # I path concreti differiscono (tmp dir diverse) ma i nomi
        # relativi devono combaciare.
        assert (
            sorted(p.name for p in bundle_from_path.imdf_geojson_files)
            == sorted(p.name for p in bundle_from_bytes.imdf_geojson_files)
        )
    finally:
        bundle_from_path.cleanup()
        bundle_from_bytes.cleanup()


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


def test_bundle_files_accessor_falls_back_to_filesystem_when_empty(tmp_path):
    """Path-based `extract_imdf_zip` non popola `bundle.files` per i
    geojson/manifest? Wait — IMDF-2 ora le popola da entrambi i path
    (l'extract reader e' condiviso). Questo test pinna il fallback
    quando `bundle.files` viene svuotato manualmente (es. dopo persist
    che potrebbe rilasciarli per liberare RAM)."""
    from app.core.imdf.loader import extract_imdf_zip

    data = _build_minimal_imdf_zip_bytes()
    zip_on_disk = tmp_path / "imdf.zip"
    zip_on_disk.write_bytes(data)

    bundle = extract_imdf_zip(str(zip_on_disk))
    try:
        # Svuotiamo `files` per simulare il post-persist scenario.
        bundle.files = {}
        # Accessor cadde sul fallback filesystem via `manifest_path`.
        assert bundle.manifest() == {"sites": {"SITE_X": {"name": "Site X"}}}
        assert bundle.has_manifest is True  # manifest_path popolato
    finally:
        bundle.cleanup()


def test_bundle_manifest_missing_returns_empty_dict():
    """Quando il manifest non e' presente in nessuna delle due shape
    (`files` vuoto + `manifest_path` None), gli accessor ritornano
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
