"""
Tests the artifact characterization behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.utils.result_store import get_result_as_tempfile


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_get_result_as_tempfile_returns_none_when_latest_result_missing(monkeypatch):
    import app.core.results.db as results_db

    monkeypatch.setattr(results_db, "fetchone", lambda *args, **kwargs: None)

    assert get_result_as_tempfile("proj-1") is None


def test_get_result_as_tempfile_uses_result_format_as_suffix(monkeypatch, tmp_path):
    import app.core.results.blob as result_blobs
    import app.core.results.db as results_db
    import app.utils.result_store as result_store

    monkeypatch.setattr(
        results_db,
        "fetchone",
        lambda *args, **kwargs: {"id": "res-1", "format": "parquet"},
    )
    monkeypatch.setattr(result_blobs, "get_result_data", lambda result_id: b"PAR1")
    monkeypatch.setattr(
        result_store,
        "settings",
        SimpleNamespace(DATA_DIR=tmp_path),
        raising=False,
    )

    path = get_result_as_tempfile("proj-1")

    assert isinstance(path, Path)
    assert path.suffix == ".parquet"
