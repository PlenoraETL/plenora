"""Contracts for the horizontal tabular dataframe helper boundary."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared]

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _runtime_imports_old_files_dataframe_io() -> list[tuple[str, int, str]]:
    roots = (_REPO_ROOT / "backend" / "app", _REPO_ROOT / "backend" / "workers")
    allowed = {
        "backend/app/core/files/dataframe_io.py",
        "backend/app/core/files/geospatial_io.py",
    }
    hits: list[tuple[str, int, str]] = []
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(_REPO_ROOT).as_posix()
            if rel in allowed:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module in {
                        "app.core.files.dataframe_io",
                        "app.core.files.geospatial_io",
                    }:
                        hits.append((rel, node.lineno, node.module))
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in {
                            "app.core.files.dataframe_io",
                            "app.core.files.geospatial_io",
                        }:
                            hits.append((rel, node.lineno, alias.name))
    return hits


def test_runtime_code_does_not_import_legacy_files_dataframe_io_facades() -> None:
    hits = _runtime_imports_old_files_dataframe_io()

    assert hits == [], (
        "Runtime code should import dataframe I/O through app.core.dataframes.*; "
        "app.core.files.*_io is compatibility-only. Hits: "
        f"{hits}"
    )


def test_core_files_domain_does_not_import_pandas_or_geopandas_directly() -> None:
    files_root = _REPO_ROOT / "backend" / "app" / "core" / "files"
    hits: list[tuple[str, int, str]] = []
    for path in sorted(files_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        rel = path.relative_to(_REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in {"pandas", "geopandas"}:
                        hits.append((rel, node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.split(".")[0] in {"pandas", "geopandas"}:
                    hits.append((rel, node.lineno, node.module))

    assert hits == [], (
        "app.core.files is a storage/upload domain; pandas/geopandas must stay "
        f"behind app.core.dataframes.*. Hits: {hits}"
    )


def test_files_dataframe_io_reexports_tabular_helpers_from_dataframes_boundary() -> None:
    from app.core.dataframes import tabular
    from app.core.files import dataframe_io

    assert dataframe_io._compact_whole_number_floats is tabular._compact_whole_number_floats
    assert dataframe_io._read_csv_safe is tabular._read_csv_safe
    assert dataframe_io.append_record_rows is tabular.append_record_rows
    assert dataframe_io.concat_dataframes is tabular.concat_dataframes
    assert dataframe_io.constant_object_series is tabular.constant_object_series
    assert dataframe_io.coerce_datetime_series is tabular.coerce_datetime_series
    assert dataframe_io.coerce_numeric_series is tabular.coerce_numeric_series
    assert dataframe_io.cut_numeric_series is tabular.cut_numeric_series
    assert dataframe_io.dataframe_from_mapping is tabular.dataframe_from_mapping
    assert dataframe_io.dataframe_from_rows is tabular.dataframe_from_rows
    assert dataframe_io.dataframe_eval_namespace is tabular.dataframe_eval_namespace
    assert dataframe_io.empty_dataframe is tabular.empty_dataframe
    assert dataframe_io.dataframe_preview_records is tabular.dataframe_preview_records
    assert dataframe_io.dataframe_to_rest_records is tabular.dataframe_to_rest_records
    assert dataframe_io.drop_rows_reset_index is tabular.drop_rows_reset_index
    assert dataframe_io.empty_object_series is tabular.empty_object_series
    assert dataframe_io.false_boolean_series is tabular.false_boolean_series
    assert dataframe_io.filter_visible_columns is tabular.filter_visible_columns
    assert dataframe_io.is_bool_series is tabular.is_bool_series
    assert dataframe_io.is_float_series is tabular.is_float_series
    assert dataframe_io.is_multiindex is tabular.is_multiindex
    assert dataframe_io.is_numeric_series is tabular.is_numeric_series
    assert dataframe_io.iter_dataframe_rows is tabular.iter_dataframe_rows
    assert dataframe_io.join_columns_as_strings is tabular.join_columns_as_strings
    assert dataframe_io.melt_dataframe is tabular.melt_dataframe
    assert dataframe_io.merge_dataframes is tabular.merge_dataframes
    assert dataframe_io.missing_scalar is tabular.missing_scalar
    assert dataframe_io.normalize_json_records is tabular.normalize_json_records
    assert dataframe_io.object_series is tabular.object_series
    assert dataframe_io.pivot_table_dataframe is tabular.pivot_table_dataframe
    assert dataframe_io.sanitize_for_json is tabular.sanitize_for_json
    assert dataframe_io.tabular_dataframe_to_bytes is tabular.tabular_dataframe_to_bytes


def test_dataframe_to_rest_records_normalizes_rest_payload_values() -> None:
    import math
    import pandas as pd
    from app.core.dataframes.tabular import dataframe_to_rest_records

    df = pd.DataFrame(
        [
            {"id": 1, "name": "Alpha", "score": math.nan, "when": pd.Timestamp("2026-01-02")},
        ]
    )

    assert dataframe_to_rest_records(df) == (
        {"id": 1, "name": "Alpha", "score": None, "when": "2026-01-02 00:00:00"},
    )


def test_files_dataframe_io_reexports_frame_io_boundary() -> None:
    from app.core.dataframes import frame_io
    from app.core.files import dataframe_io

    assert dataframe_io.DefaultFrameFileIO is frame_io.DefaultFrameFileIO
    assert dataframe_io.find_latest_output_file is frame_io.find_latest_output_file
    assert dataframe_io.load_dataframe_from_file is frame_io.load_dataframe_from_file
    assert dataframe_io.save_dataframe is frame_io.save_dataframe
    assert dataframe_io.save_dataframe_to_bytes is frame_io.save_dataframe_to_bytes
    assert dataframe_io.save_tabular_dataframe is frame_io.save_tabular_dataframe


def test_files_geospatial_io_reexports_dataframe_boundary() -> None:
    from app.core.dataframes import geospatial_io as dataframe_geospatial
    from app.core.files import geospatial_io as files_geospatial

    assert (
        files_geospatial.geospatial_dataframe_to_bytes
        is dataframe_geospatial.geospatial_dataframe_to_bytes
    )
    assert (
        files_geospatial.save_geospatial_dataframe
        is dataframe_geospatial.save_geospatial_dataframe
    )


def test_files_load_dispatch_reexports_dataframe_dispatch_boundary() -> None:
    from app.core.dataframes import load_dispatch as dataframe_dispatch
    from app.core.dataframes import ports
    from app.core.files import load_dispatch as files_dispatch

    assert dataframe_dispatch.DataLoaderProtocol is ports.DataLoaderProtocol
    assert dataframe_dispatch.FrameIOEngine is ports.FrameIOEngine
    assert dataframe_dispatch.LoadedFrame is ports.LoadedFrame
    assert dataframe_dispatch.LoggerProtocol is ports.LoggerProtocol
    assert files_dispatch.DataLoaderProtocol is ports.DataLoaderProtocol
    assert files_dispatch.FrameIOEngine is ports.FrameIOEngine
    assert files_dispatch.LoadedFrame is ports.LoadedFrame
    assert files_dispatch.LoggerProtocol is ports.LoggerProtocol
    assert files_dispatch.load_by_format is dataframe_dispatch.load_by_format
    assert files_dispatch.load_frame_by_format is dataframe_dispatch.load_frame_by_format


def test_load_frame_by_format_returns_loaded_frame_shape() -> None:
    import pandas as pd
    from app.core.dataframes.load_dispatch import LoadedFrame, load_frame_by_format

    class Loader:
        def load_csv(self, _path):
            return pd.DataFrame({"x": [1]})

    class Logger:
        def info(self, _message):
            return None

    loaded = load_frame_by_format(
        Loader(),
        "ignored.csv",
        "csv",
        logger=Logger(),
    )

    assert isinstance(loaded, LoadedFrame)
    assert loaded.gdf is None
    assert loaded.format_type == "csv"
    assert loaded.df.to_dict(orient="records") == [{"x": 1}]
