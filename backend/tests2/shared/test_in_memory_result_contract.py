"""
Contract test — in-memory pipeline result storage.

Cintura anti-regression + TDD per il refactor che elimina il filesystem
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Helpers: fixture base DB-mock + fake settings


@pytest.fixture
def mock_db(monkeypatch):
    """Mock db_service minimale per save_result_blob* flows."""
    import app.core.projects.runtime_context as project_runtime_context
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    saved: dict = {}

    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-1"))
    monkeypatch.setattr(
        project_runtime_context,
        "resolve_project_runtime_group_id",
        lambda pid, *, default_group: "grp-1",
    )
    monkeypatch.setattr(results_db, "save_result_row",
                        lambda **kwargs: saved.update(kwargs))
    monkeypatch.setattr(result_store, "_apply_results_retention",
                        lambda pid: None)

    # Evita dependency su settings per MAX_RESULT_BLOB_SIZE
    class _FakeSettings:
        @staticmethod
        def get(key, default=None):
            return default
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

    return saved


# 1. REGRESSION — path-based save_result_blob


def test_regression_path_based_save_preserves_bytes_exactly(mock_db, tmp_path):
    """Il blob salvato su DB deve essere byte-identico al file temp."""
    from app.utils.result_store import save_result_blob

    f = tmp_path / "r.csv"
    f.write_bytes(b"id,name\n1,alice\n2,bob\n")

    result_id = save_result_blob("proj-1", f, "csv", rows_count=2)
    assert result_id == "res-1"
    assert mock_db["data"] == b"id,name\n1,alice\n2,bob\n"
    assert not f.exists(), "temp file deve essere cancellato dopo success"


def test_regression_path_based_save_keeps_temp_on_db_error(monkeypatch, tmp_path):
    """Se il DB fallisce, il temp file NON deve essere cancellato (retry-friendly)."""
    from app.utils.result_store import save_result_blob, ResultSaveError
    import app.core.projects.runtime_context as project_runtime_context
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    def _boom(**kwargs):
        raise RuntimeError("DB down")

    monkeypatch.setattr(result_store, "uuid",
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
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

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
    from app.core.files.dataframe_io import save_dataframe_to_bytes

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
    from app.core.files.dataframe_io import save_dataframe_to_bytes
    from shapely.geometry import Point

    df = pd.DataFrame({
        "id": [1, 2],
        "geometry": [Point(9.0, 45.0), Point(9.1, 45.1)],
    })

    data = save_dataframe_to_bytes(df, "geojson", geom_column="geometry")
    obj = json.loads(data.decode("utf-8"))
    assert obj.get("type") == "FeatureCollection"
    assert len(obj.get("features", [])) == 2


def test_save_dataframe_to_bytes_rejects_filesystem_only_formats():
    """gpkg e shp richiedono filesystem (tool binari / multi-file zip) →
    il nuovo API NON li accetta in-memory. Il caller fa fallback al
    path-based save_dataframe se serve questi formati."""
    from app.core.files.dataframe_io import save_dataframe_to_bytes

    df = pd.DataFrame({"id": [1]})
    for fs_only in ("gpkg", "shp"):
        with pytest.raises((ValueError, NotImplementedError)):
            save_dataframe_to_bytes(df, fs_only)


def test_save_result_blob_from_bytes_happy_path(mock_db):
    """Bytes → DB blob direttamente, zero disk."""
    from app.utils.result_store import save_result_blob_from_bytes

    payload = b"id,name\n1,alice\n"
    result_id = save_result_blob_from_bytes(
        project_id="proj-1",
        data=payload,
        filename="result.csv",
        fmt="csv",
        rows_count=1,
    )
    assert result_id == "res-1"
    assert mock_db["data"] == payload
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
    from app.utils import result_store
    import app.core.results.db as results_db
    import app.core.results.blob as result_blob

    expected = b"col1,col2\n1,a\n2,b\n"
    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: results_db.ResultMetadataRow(id="r1", format="csv"),
    )
    monkeypatch.setattr(result_blob, "get_result_data",
                        lambda rid: expected)

    result = result_store.get_result_as_bytes("proj-1")
    assert result == expected


def test_get_result_as_bytes_returns_none_when_missing(monkeypatch):
    from app.utils import result_store
    import app.core.results.db as results_db
    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: None,
    )
    assert result_store.get_result_as_bytes("missing") is None


# 3. SENTINEL no-temp-file-leak


def test_save_result_blob_from_bytes_creates_no_files_on_disk(
    mock_db, monkeypatch, tmp_path
):
    """Proof: il nuovo flusso in-memory non scrive MAI su DATA_DIR/temp.

    Mock settings.DATA_DIR a tmp_path e verifica che dopo la call
    la dir resti vuota.
    """
    import app.utils.result_store as result_store
    from app.utils.result_store import save_result_blob_from_bytes

    # Crea la dir per aver qualcosa da enumerare
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    class _FakeSettings:
        DATA_DIR = tmp_path
        @staticmethod
        def get(key, default=None):
            return default
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

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
    from app.utils import result_store
    import app.core.results.db as results_db
    import app.core.results.blob as result_blob

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    class _FakeSettings:
        DATA_DIR = tmp_path
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

    monkeypatch.setattr(
        results_db,
        "get_latest_result_row",
        lambda pid, group_id=None: results_db.ResultMetadataRow(id="r1", format="csv"),
    )
    monkeypatch.setattr(result_blob, "get_result_data",
                        lambda rid: b"payload")

    result_store.get_result_as_bytes("proj-1")

    residui = list(temp_dir.rglob("*"))
    assert residui == [], (
        f"get_result_as_bytes deve essere pure in-memory. "
        f"Residui in tmp: {residui}"
    )
