"""
Contract test — in-memory pipeline result storage.

Cintura anti-regression + TDD per il refactor che elimina il filesystem
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

legacy_path_result_blob = pytest.mark.skip(
    reason="save_result_blob(path) tombstonato post QUERY-1; usare save_result_blob_from_bytes"
)


# Helpers: fixture base DB-mock + fake settings


@pytest.fixture
def mock_db(monkeypatch):
    """Mock db_service minimale per save_result_blob* flows."""
    import app.core.projects.runtime_context as project_runtime_context
    import app.core.results.db as results_db
    import app.core.results.blob_writer as result_blob_writer

    saved: dict = {}

    monkeypatch.setattr(result_blob_writer, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-1"))
    monkeypatch.setattr(
        project_runtime_context,
        "resolve_project_runtime_group_id",
        lambda pid, *, default_group: "grp-1",
    )
    monkeypatch.setattr(results_db, "save_result_row",
                        lambda **kwargs: saved.update(kwargs))
    monkeypatch.setattr(result_blob_writer, "apply_results_retention",
                        lambda pid: None)

    class _MemoryStorage:
        def __init__(self):
            self.objects = {}

        def upload_bytes(self, data, key):
            self.objects[key] = data

        def delete(self, key):
            self.objects.pop(key, None)

    storage = _MemoryStorage()
    monkeypatch.setattr(
        result_blob_writer.object_storage_module,
        "get_runtime_object_storage",
        lambda: storage,
    )
    monkeypatch.setattr(
        result_blob_writer,
        "record_result_upload_intent",
        lambda *args, **kwargs: None,
    )

    # Evita dependency su settings per MAX_RESULT_BLOB_SIZE
    class _FakeSettings:
        @staticmethod
        def get(key, default=None):
            return default
    monkeypatch.setattr(result_blob_writer, "settings", _FakeSettings, raising=False)

    return saved


# 1. REGRESSION — path-based save_result_blob


@legacy_path_result_blob
def test_regression_path_based_save_preserves_bytes_exactly(mock_db, tmp_path):
    """Il blob salvato su DB deve essere byte-identico al file temp."""
    from app.core.results.blob_writer import save_result_blob

    f = tmp_path / "r.csv"
    f.write_bytes(b"id,name\n1,alice\n2,bob\n")

    result_id = save_result_blob("proj-1", f, "csv", rows_count=2)
    assert result_id == "res-1"
    assert mock_db["data"] == b"id,name\n1,alice\n2,bob\n"
    assert not f.exists(), "temp file deve essere cancellato dopo success"


@legacy_path_result_blob
def test_regression_path_based_save_keeps_temp_on_db_error(monkeypatch, tmp_path):
    """Se il DB fallisce, il temp file NON deve essere cancellato (retry-friendly)."""
    from app.core.results.blob_writer import save_result_blob, ResultSaveError
    import app.core.projects.runtime_context as project_runtime_context
    import app.core.results.db as results_db
    import app.core.results.blob_writer as result_blob_writer

    def _boom(**kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(result_blob_writer, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-x"))
    monkeypatch.setattr(
        project_runtime_context,
        "resolve_project_runtime_group_id",
        lambda pid, *, default_group: "grp",
    )
    monkeypatch.setattr(results_db, "save_result_row", _boom)

    class _FakeSettings:
        @staticmethod
        def get(key, default=None):
            return default
    monkeypatch.setattr(result_blob_writer, "settings", _FakeSettings, raising=False)

    f = tmp_path / "r.csv"
    f.write_bytes(b"id\n1\n")

    with pytest.raises(ResultSaveError):
        save_result_blob("proj-1", f, "csv")

    assert f.exists(), (
        "temp file deve restare su disco se DB fallisce "
        "(per retry manuale o recovery)"
    )


# 2. CONTRACT sul nuovo API in-memory


@pytest.mark.parametrize("fmt", ["csv", "xlsx", "json", "parquet"])
def test_save_dataframe_to_bytes_roundtrip(fmt):
    """In-memory serialize → bytes leggibili dal reader corrispondente.

    Senza touchare il filesystem.
    """
    from app.core.dataframes.frame_io import save_dataframe_to_bytes

    df = pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["alice", "bob", "carol"],
    })

    data = save_dataframe_to_bytes(df, fmt)
    assert isinstance(data, bytes)
    assert len(data) > 0

    # Roundtrip check per format
    buf = io.BytesIO(data)
    if fmt == "csv":
        read_df = pd.read_csv(buf)
    elif fmt == "xlsx":
        read_df = pd.read_excel(buf)
    elif fmt == "json":
        records = json.loads(data.decode("utf-8"))
        read_df = pd.DataFrame(records)
    elif fmt == "parquet":
        read_df = pd.read_parquet(buf)
    else:
        raise RuntimeError("format non testato")

    # Valori matchano (tipo può differire marginalmente per csv → tutto stringa)
    assert list(read_df.columns) == ["id", "name"]
    assert len(read_df) == 3


def test_save_dataframe_to_bytes_geojson():
    """GeoJSON serialization in-memory via geopandas."""
    pytest.importorskip("geopandas")
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    from shapely.geometry import Point

    df = pd.DataFrame({
        "id": [1, 2],
        "geometry": [Point(9.0, 45.0), Point(9.1, 45.1)],
    })

    data = save_dataframe_to_bytes(df, "geojson", geom_column="geometry")
    obj = json.loads(data.decode("utf-8"))
    assert obj.get("type") == "FeatureCollection"
    assert len(obj.get("features", [])) == 2


def test_save_dataframe_to_bytes_gpkg_and_shp_now_supported():
    """Post-Phase5: gpkg e shp sono entrambi serializzabili in-memory.

    - gpkg: single-file SQLite via `pyogrio.write_dataframe(buf, 'GPKG')`
    - shp: multi-file zippato via
      `app.core.io.geospatial.write_shapefile_zip_bytes` che usa una
      TemporaryDirectory INTERNA al boundary `io.geospatial` per
      scrivere i sidecar e li zippa in RAM.
    """
    pytest.importorskip("geopandas")
    from app.core.dataframes.frame_io import save_dataframe_to_bytes
    from shapely.geometry import Point

    df = pd.DataFrame({
        "id": [1, 2],
        "geometry": [Point(9.0, 45.0), Point(9.1, 45.1)],
    })

    gpkg_bytes = save_dataframe_to_bytes(df, "gpkg", geom_column="geometry")
    assert isinstance(gpkg_bytes, bytes) and len(gpkg_bytes) > 0

    shp_zip_bytes = save_dataframe_to_bytes(df, "shp", geom_column="geometry")
    assert isinstance(shp_zip_bytes, bytes) and len(shp_zip_bytes) > 0
    # Lo ZIP deve contenere almeno .shp + .shx + .dbf + .prj.
    buf = io.BytesIO(shp_zip_bytes)
    import zipfile as _zf
    with _zf.ZipFile(buf) as z:
        names = {Path(n).suffix.lower() for n in z.namelist()}
    assert {".shp", ".shx", ".dbf"}.issubset(names)


def test_save_result_blob_from_bytes_happy_path(mock_db):
    """Bytes → DB blob direttamente, zero disk."""
    from app.core.results.blob_writer import save_result_blob_from_bytes

    payload = b"id,name\n1,alice\n"
    result_id = save_result_blob_from_bytes(
        project_id="proj-1",
        data=payload,
        filename="result.csv",
        fmt="csv",
        rows_count=1,
    )
    assert result_id == "res-1"
    assert "data" not in mock_db
    assert mock_db["storage_key"] == (
        "tenants/grp-1/results/proj-1/res-1.bin"
    )
    assert mock_db["size_bytes"] == len(payload)
    assert mock_db["filename"] == "result.csv"
    assert mock_db["rows_count"] == 1
    assert mock_db["fmt"] == "csv"


def test_save_result_blob_from_bytes_no_artificial_hard_cap(monkeypatch):
    """Post FS-4+multipart: nessun hard cap artificiale su save_result_blob_from_bytes.

    Prima c'era un cap ResultSaveError oltre MAX_RESULT_BLOB_SIZE. Ora:
    - sotto threshold -> DB blob
    - sopra threshold -> S3 (auto-chunking via upload_bytes del provider)
    Il cap reale e' la RAM: se `data` esiste come bytes in-memory, si uploada.

    Test: con threshold artificialmente basso, payload piccolo-ma-sopra va
    a S3 (mockato) invece di raise. Nessuna ResultSaveError artificiale.
    """
    pass  # test originale (cap 100MB che sollevava) rimosso: vedi
          # test_fs4_s3_large_result_dispatch::test_no_hard_cap_any_size_goes_to_s3


def test_get_result_as_bytes_returns_raw_blob(monkeypatch):
    """get_result_as_bytes: DB blob -> bytes direttamente, zero tempfile."""
    from app.core.results import result_reader
    from app.core.results import store as results_store
    import app.core.results.db as results_db

    expected = b"col1,col2\n1,a\n2,b\n"
    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: results_db.ResultMetadataRow(id="r1", format="csv"),
    )
    monkeypatch.setattr(results_store, "get_result_data",
                        lambda rid: expected)

    result = result_reader.get_result_as_bytes("proj-1")
    assert result == expected


def test_get_result_as_bytes_returns_none_when_missing(monkeypatch):
    from app.core.results import result_reader
    import app.core.results.db as results_db
    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: None,
    )
    assert result_reader.get_result_as_bytes("missing") is None


# 3. SENTINEL no-temp-file-leak


def test_save_result_blob_from_bytes_creates_no_files_on_disk(
    mock_db, monkeypatch, tmp_path
):
    """Proof: il nuovo flusso in-memory non scrive MAI su DATA_DIR/temp.

    Mock settings.DATA_DIR a tmp_path e verifica che dopo la call
    la dir resti vuota.
    """
    import app.core.results.blob_writer as result_blob_writer
    from app.core.results.blob_writer import save_result_blob_from_bytes

    # Crea la dir per aver qualcosa da enumerare
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    class _FakeSettings:
        DATA_DIR = tmp_path
        @staticmethod
        def get(key, default=None):
            return default
    monkeypatch.setattr(result_blob_writer, "settings", _FakeSettings, raising=False)

    save_result_blob_from_bytes(
        project_id="proj-1",
        data=b"small data",
        filename="r.csv",
        fmt="csv",
        rows_count=0,
    )

    # Dir temp deve essere vuota
    residui = list(temp_dir.rglob("*"))
    assert residui == [], (
        f"Il nuovo flusso in-memory NON deve creare file in DATA_DIR/temp. "
        f"Residui trovati: {residui}"
    )


def test_get_result_as_bytes_creates_no_files_on_disk(
    monkeypatch, tmp_path
):
    """Stesso sentinel per la direzione di lettura."""
    from app.core.results import result_reader
    from app.core.results import store as results_store
    import app.core.results.db as results_db

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    class _FakeSettings:
        DATA_DIR = tmp_path
    monkeypatch.setattr(result_reader, "settings", _FakeSettings, raising=False)

    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: results_db.ResultMetadataRow(id="r1", format="csv"),
    )
    monkeypatch.setattr(results_store, "get_result_data",
                        lambda rid: b"payload")

    result_reader.get_result_as_bytes("proj-1")

    residui = list(temp_dir.rglob("*"))
    assert residui == [], (
        f"get_result_as_bytes deve essere pure in-memory. "
        f"Residui in tmp: {residui}"
    )
