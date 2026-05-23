"""Sessione 4 contract test: data-layer copy/dtype P0/P1 residui.

Bug coperti:
1. data-layer P1 — `tabular.filter_visible_columns` no `.copy()` → caller
   mutava silenziosamente il DataFrame di input.
2. data-layer P1 — `frame_io.load_dataframe_from_file` json branch
   mutation in-place duplicata vs `_coerce_json_dataframe_columns`.
3. data-layer P0 — `source_blob.temp_file_for` tempfile leak senza
   cleanup garantito sul caller.
"""

from __future__ import annotations

import inspect
import json

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Path 1: filter_visible_columns ritorna copia indipendente

def test_filter_visible_columns_does_not_mutate_input_df():
    """Il caller che modifica il risultato NON deve corrompere il df
    originale. Pre-fix: `df[raw_names]` era view su CoW disabilitato."""
    from app.core.dataframes.tabular import filter_visible_columns

    df_in = pd.DataFrame({
        "a": [1, 2, 3],
        "b": [4, 5, 6],
        "c": [7, 8, 9],
    })
    node_columns = [
        {"name": "a", "visible": True},
        {"name": "b", "visible": True},
    ]

    result = filter_visible_columns(df_in, node_columns)

    # Modifica result; df_in NON deve cambiare
    result.iloc[0, 0] = 999
    assert df_in.iloc[0, 0] == 1, (
        "Mutazione di result ha propagato al df di input: filter_visible_columns "
        "ritorna view invece di copia. Pre-fix P1 data-layer."
    )


def test_filter_visible_columns_with_alias_returns_copy():
    """Stesso contract con alias_map (path piu' complesso)."""
    from app.core.dataframes.tabular import filter_visible_columns

    df_in = pd.DataFrame({"original_name": [1, 2, 3]})
    node_columns = [{"name": "original_name", "alias": "renamed", "visible": True}]

    result = filter_visible_columns(df_in, node_columns)
    assert "renamed" in result.columns
    # df_in non rinominato
    assert "original_name" in df_in.columns
    assert "renamed" not in df_in.columns


# Path 2: json load delegato a _coerce_json_dataframe_columns

def test_load_dataframe_json_uses_canonical_coerce_helper(tmp_path):
    """Anti-regression: il branch json di load_dataframe_from_file deve
    delegare a `_coerce_json_dataframe_columns` invece di duplicare il
    loop di coercion (rischio drift fra le 2 implementazioni)."""
    from app.core.dataframes import frame_io

    src = inspect.getsource(frame_io.load_dataframe_from_file)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Il branch .json deve chiamare _coerce_json_dataframe_columns
    json_idx = src_no_comments.find('estensione == ".json"')
    assert json_idx >= 0, "Branch json non trovato"
    window = src_no_comments[json_idx:json_idx + 1000]
    assert "_coerce_json_dataframe_columns" in window, (
        "Branch .json deve delegare a _coerce_json_dataframe_columns "
        "(helper canonico) invece di loop in-place duplicato"
    )
    # Anti-regression: nessun loop `df[col] = converted` inline
    # Sintassi Python esatta - assignment a colonna dataframe
    assert "df[col] = converted" not in window, (
        "Detected mutation in-place residua nel branch .json: usa helper"
    )


def test_load_dataframe_json_coerces_numeric_columns(tmp_path):
    """Functional: il branch json deve ancora produrre coercion numeric
    quando le colonne sono 100% numeriche."""
    from app.core.dataframes.frame_io import load_dataframe_from_file

    json_file = tmp_path / "data.json"
    json_file.write_text(json.dumps([
        {"id": "1", "value": "10.5", "label": "alpha"},
        {"id": "2", "value": "20.0", "label": "beta"},
        {"id": "3", "value": "30.5", "label": "gamma"},
    ]))

    df = load_dataframe_from_file(json_file)

    # Coercion ha funzionato: id/value sono numerici
    assert pd.api.types.is_numeric_dtype(df["id"])
    assert pd.api.types.is_numeric_dtype(df["value"])
    # label resta string
    assert df["label"].dtype == "object"


# Path 3: temp_file_for context manager

def test_temp_file_for_cm_yields_path_and_unlinks(tmp_path):
    """temp_file_for_cm cancella il tempfile al with-exit
    deterministicamente."""
    from app.core.files import source_blob

    blob_data = b"fake-csv-content"
    captured_path = None

    def _db_get(file_id, include_data):
        return {"format": "csv", "original_filename": "test.csv"}

    def _get_blob(file_id, group_id):
        return blob_data

    def _mkdtemp(dir=None):
        return tmp_path / "shp_extract"

    def _safe_extract(zip_archive, target):
        pass

    def _rmtree_safe(path):
        import shutil
        shutil.rmtree(path, ignore_errors=True)

    class _SilentLogger:
        def warning(self, *a, **kw): pass
        def error(self, *a, **kw): pass
        def info(self, *a, **kw): pass
        def debug(self, *a, **kw): pass

    with source_blob.temp_file_for_cm(
        "file-1",
        db_get_file_source=_db_get,
        get_blob_from_db=_get_blob,
        temp_dir=str(tmp_path),
        mkdtemp_writable=_mkdtemp,
        safe_extractall=_safe_extract,
        rmtree_safe=_rmtree_safe,
        logger=_SilentLogger(),
    ) as path:
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == blob_data
        captured_path = path

    # ANTI-REGRESSION: il tempfile DEVE essere cancellato al with-exit
    assert not captured_path.exists(), (
        "temp_file_for_cm NON ha cancellato il tempfile al with-exit. "
        "Senza cleanup deterministico, l'uso del CM non elimina il leak."
    )


def test_temp_file_for_atexit_registry_registered():
    """Architectural lint: temp_file_for legacy registra atexit cleanup
    per safety net su orphan tempfile."""
    from app.core.files import source_blob

    src = inspect.getsource(source_blob.temp_file_for)
    assert "_register_tempfile_atexit_cleanup" in src, (
        "temp_file_for deve registrare il tempfile in atexit registry "
        "come safety net (caller cleanup non garantito)"
    )
