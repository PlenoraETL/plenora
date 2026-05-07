"""Contract test — gpkg/shp usano TemporaryDirectory, NON DATA_DIR/temp.

Post-hardening (opzione B del piano FS-residual):
  - gpkg/shp per save_pipeline_result e enrichment_task eseguibile
    non usano piu' `DATA_DIR/temp/urby_<pid>_<hash>/` (che orfana su
    SIGKILL) ma `tempfile.TemporaryDirectory()` (auto-cleanup al
    __exit__ + pulizia OS di /tmp a reboot).
  - Upload via bytes (save_result_blob_from_bytes / eseguibili.storage.save_bytes)
    — nessun path cleanup sovrapposto che possa confliggere col
    context manager.

Sentinel:
  1. Dopo save_pipeline_result(gpkg): DATA_DIR/temp/ resta vuota
  2. Il path usato nel save_dataframe inizia con `urby_gpkg_shp_` (o
     `urby_eseg_gpkg_shp_`), NON con `DATA_DIR/temp/urby_<pid>_<hash>/`
  3. Anche su upload failure (save_result_blob_from_bytes raise),
     la tmp_dir viene comunque pulita.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# save_pipeline_result gpkg/shp branch


def test_save_pipeline_result_gpkg_no_file_in_data_dir_temp(monkeypatch, tmp_path):
    """Sentinel: DATA_DIR/temp/ resta vuota dopo save_pipeline_result(gpkg)."""
    from app.utils.result_store import save_pipeline_result
    import app.utils.result_store as result_store
    import app.db.db_service as db_service

    # Simula DATA_DIR su tmp_path per intercettare eventuali file residui
    data_dir = tmp_path / "data"
    temp_dir = data_dir / "temp"
    temp_dir.mkdir(parents=True)

    class _FakeSettings:
        DATA_DIR = data_dir
        @staticmethod
        def get(key, default=None): return default
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

    # Mock save_dataframe (fs-only writer) per isolare il test
    def _fake_save(df, output_path, fmt, *a, **kw):
        # Scrive un file fake nella dir che riceve (TemporaryDirectory)
        out = Path(str(output_path)).with_suffix(f".{fmt}")
        out.write_bytes(b"fake-gpkg-content")
        return out
    monkeypatch.setattr("app.utils.dataframe.save_dataframe", _fake_save)

    # Mock DB save
    saved_db_calls = []
    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-gpkg"))
    monkeypatch.setattr(db_service, "get_project",
                        lambda pid: {"id": pid, "group_id": "grp"})
    monkeypatch.setattr(db_service, "save_result",
                        lambda **kw: saved_db_calls.append(kw))
    monkeypatch.setattr(result_store, "_apply_results_retention",
                        lambda pid: None)

    df = pd.DataFrame({"a": [1, 2, 3]})
    result = save_pipeline_result(
        project_id="p1", df=df, output_format="gpkg",
        filename_stem="report", rows_count=3,
    )

    # Contract dict shape
    assert result["result_id"] == "res-gpkg"
    assert result["output_filename"] == "report.gpkg"

    # SENTINEL: DATA_DIR/temp/ resta vuota (no urby_<pid>_<hash> creato)
    residui = list(temp_dir.rglob("*"))
    assert residui == [], (
        f"DATA_DIR/temp NON deve contenere residui post save_pipeline_result(gpkg). "
        f"Trovati: {residui}"
    )

    # DB save usa bytes (da read_bytes del file temporaneo in /tmp OS)
    assert len(saved_db_calls) == 1
    assert saved_db_calls[0]["data"] == b"fake-gpkg-content"


def test_save_pipeline_result_gpkg_uses_temporary_directory_prefix(monkeypatch, tmp_path):
    """Sentinel: il path passato a save_dataframe e' dentro tempfile.TemporaryDirectory
    con prefix urby_gpkg_shp_ (non DATA_DIR/temp/urby_<pid>_<hash>/)."""
    from app.utils.result_store import save_pipeline_result
    import app.utils.result_store as result_store
    import app.db.db_service as db_service

    # Settings DATA_DIR
    class _FakeSettings:
        DATA_DIR = tmp_path
        @staticmethod
        def get(key, default=None): return default
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

    captured_paths = []

    def _spy_save(df, output_path, fmt, *a, **kw):
        captured_paths.append(str(output_path))
        out = Path(str(output_path)).with_suffix(f".{fmt}")
        out.write_bytes(b"x")
        return out
    monkeypatch.setattr("app.utils.dataframe.save_dataframe", _spy_save)

    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-1"))
    monkeypatch.setattr(db_service, "get_project",
                        lambda pid: {"id": pid, "group_id": "grp"})
    monkeypatch.setattr(db_service, "save_result", lambda **kw: None)
    monkeypatch.setattr(result_store, "_apply_results_retention",
                        lambda pid: None)

    df = pd.DataFrame({"a": [1]})
    save_pipeline_result(
        project_id="p1", df=df, output_format="gpkg",
        filename_stem="r", rows_count=1,
    )

    assert len(captured_paths) == 1
    # Il path usato deve contenere il prefix urby_gpkg_shp_ (TemporaryDirectory)
    path_used = captured_paths[0]
    assert "urby_gpkg_shp_" in path_used, (
        f"Path atteso dentro TemporaryDirectory con prefix urby_gpkg_shp_, "
        f"ricevuto: {path_used}"
    )


def test_save_pipeline_result_gpkg_cleans_tmp_dir_on_upload_failure(monkeypatch, tmp_path):
    """Su upload failure, il context exit pulisce comunque la tmp_dir."""
    from app.utils.result_store import save_pipeline_result
    import app.utils.result_store as result_store
    import app.db.db_service as db_service

    class _FakeSettings:
        DATA_DIR = tmp_path
        @staticmethod
        def get(key, default=None): return default
    monkeypatch.setattr(result_store, "settings", _FakeSettings, raising=False)

    captured_tmp_dirs = []

    def _spy_save(df, output_path, fmt, *a, **kw):
        out = Path(str(output_path)).with_suffix(f".{fmt}")
        out.write_bytes(b"content")
        captured_tmp_dirs.append(out.parent)
        return out
    monkeypatch.setattr("app.utils.dataframe.save_dataframe", _spy_save)

    # save_result (DB) solleva
    monkeypatch.setattr(result_store, "uuid",
                        SimpleNamespace(uuid4=lambda: "res-fail"))
    monkeypatch.setattr(db_service, "get_project",
                        lambda pid: {"id": pid, "group_id": "grp"})
    def _boom(**kw):
        raise RuntimeError("DB unavailable")
    monkeypatch.setattr(db_service, "save_result", _boom)

    df = pd.DataFrame({"a": [1]})
    with pytest.raises(Exception):
        save_pipeline_result(
            project_id="p1", df=df, output_format="gpkg",
            filename_stem="r", rows_count=1,
        )

    # Tmp dir usata da TemporaryDirectory rimossa dal context exit
    assert len(captured_tmp_dirs) == 1
    tmp_dir_used = captured_tmp_dirs[0]
    assert not tmp_dir_used.exists(), (
        f"TemporaryDirectory __exit__ NON ha pulito {tmp_dir_used} "
        f"dopo upload failure"
    )
