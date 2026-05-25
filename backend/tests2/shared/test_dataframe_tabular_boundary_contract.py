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
    assert dataframe_io.dataframe_first_column is tabular.dataframe_first_column
    assert dataframe_io.dataframe_first_row_dict is tabular.dataframe_first_row_dict
    assert dataframe_io.dataframe_records is tabular.dataframe_records
    assert dataframe_io.dataframe_row_by_index is tabular.dataframe_row_by_index
    assert dataframe_io.dataframe_row_dict is tabular.dataframe_row_dict
    assert dataframe_io.duplicate_row_count is tabular.duplicate_row_count
    assert dataframe_io.drop_duplicate_columns is tabular.drop_duplicate_columns
    assert dataframe_io.drop_duplicate_rows_reset_index is tabular.drop_duplicate_rows_reset_index
    assert dataframe_io.empty_dataframe is tabular.empty_dataframe
    assert dataframe_io.dataframe_preview_records is tabular.dataframe_preview_records
    assert dataframe_io.iter_dataframe_rest_records is tabular.iter_dataframe_rest_records
    assert dataframe_io.drop_rows_reset_index is tabular.drop_rows_reset_index
    assert dataframe_io.empty_object_series is tabular.empty_object_series
    assert dataframe_io.false_boolean_series is tabular.false_boolean_series
    assert dataframe_io.filter_visible_columns is tabular.filter_visible_columns
    assert dataframe_io.float_series_non_null_whole_numbers is tabular.float_series_non_null_whole_numbers
    assert dataframe_io.geometry_series_columns_to_wkt is tabular.geometry_series_columns_to_wkt
    assert dataframe_io.group_aggregate_as_columns is tabular.group_aggregate_as_columns
    assert dataframe_io.group_aggregate_reset is tabular.group_aggregate_reset
    assert dataframe_io.group_cumcount_series is tabular.group_cumcount_series
    assert dataframe_io.group_keys_dataframe is tabular.group_keys_dataframe
    assert dataframe_io.group_named_aggregate_merge is tabular.group_named_aggregate_merge
    assert dataframe_io.group_size_reset is tabular.group_size_reset
    assert dataframe_io.group_size_values is tabular.group_size_values
    assert dataframe_io.is_bool_series is tabular.is_bool_series
    assert dataframe_io.is_float_series is tabular.is_float_series
    assert dataframe_io.is_multiindex is tabular.is_multiindex
    assert dataframe_io.is_numeric_series is tabular.is_numeric_series
    assert dataframe_io.iter_grouped_dataframes is tabular.iter_grouped_dataframes
    assert dataframe_io.iter_dataframe_indexed_rows is tabular.iter_dataframe_indexed_rows
    assert dataframe_io.iter_dataframe_rows is tabular.iter_dataframe_rows
    assert dataframe_io.join_columns_as_strings is tabular.join_columns_as_strings
    assert dataframe_io.estimate_merge_output_rows is tabular.estimate_merge_output_rows
    assert dataframe_io.matching_row_indices is tabular.matching_row_indices
    assert dataframe_io.melt_dataframe is tabular.melt_dataframe
    assert dataframe_io.merge_dataframes is tabular.merge_dataframes
    assert dataframe_io.missing_scalar is tabular.missing_scalar
    assert dataframe_io.mixed_numeric_string_object_columns is tabular.mixed_numeric_string_object_columns
    assert dataframe_io.non_null_head_values is tabular.non_null_head_values
    assert dataframe_io.normalize_json_records is tabular.normalize_json_records
    assert dataframe_io.null_count_for_columns is tabular.null_count_for_columns
    assert dataframe_io.object_series is tabular.object_series
    assert dataframe_io.pivot_table_dataframe is tabular.pivot_table_dataframe
    assert dataframe_io.sample_dataframe is tabular.sample_dataframe
    assert dataframe_io.sample_rows_reset_index is tabular.sample_rows_reset_index
    assert dataframe_io.sanitize_for_json is tabular.sanitize_for_json
    assert dataframe_io.select_and_rename_columns is tabular.select_and_rename_columns
    assert dataframe_io.row_indices_where_column_in is tabular.row_indices_where_column_in
    assert dataframe_io.set_cell_value is tabular.set_cell_value
    assert dataframe_io.set_values_where_mask is tabular.set_values_where_mask
    assert dataframe_io.sort_rows_reset_index is tabular.sort_rows_reset_index
    assert dataframe_io.series_null_ratio is tabular.series_null_ratio
    assert dataframe_io.stringify_columns_preserving_nulls is tabular.stringify_columns_preserving_nulls
    assert dataframe_io.tabular_dataframe_to_bytes is tabular.tabular_dataframe_to_bytes
    assert dataframe_io.unique_non_null_column_values is tabular.unique_non_null_column_values
    assert dataframe_io.unique_rows_for_columns is tabular.unique_rows_for_columns
    assert dataframe_io.window_function_values is tabular.window_function_values


def test_iter_dataframe_rest_records_normalizes_rest_payload_values() -> None:
    import math
    import pandas as pd
    from app.core.dataframes.tabular import iter_dataframe_rest_records

    df = pd.DataFrame(
        [
            {"id": 1, "name": "Alpha", "score": math.nan, "when": pd.Timestamp("2026-01-02")},
        ]
    )

    # Materializzo qui solo per assert: in produzione il consumer
    # itera lazy.
    assert tuple(iter_dataframe_rest_records(df)) == (
        {"id": 1, "name": "Alpha", "score": None, "when": "2026-01-02 00:00:00"},
    )


def test_iter_dataframe_rest_records_handles_nullable_pandas_dtypes() -> None:
    """Contract su NaN, pd.NA, nullable Int64, nullable boolean,
    timestamp. Tutti i missing devono normalizzare a `None` nel
    payload REST; i tipi nullable pandas non devono leak come
    `<NA>` o `pd.NA`."""
    import math
    import pandas as pd
    from app.core.dataframes.tabular import iter_dataframe_rest_records

    df = pd.DataFrame(
        {
            "id": pd.array([1, 2, None], dtype="Int64"),
            "flag": pd.array([True, None, False], dtype="boolean"),
            "score": [math.nan, 1.5, None],
            "name": ["a", None, "c"],
            "when": pd.to_datetime(["2026-01-01", None, "2026-01-03"]),
        }
    )

    records = list(iter_dataframe_rest_records(df))
    assert len(records) == 3

    # Row 0: tutti popolati tranne score=NaN
    assert records[0]["id"] == 1
    assert records[0]["flag"] is True
    assert records[0]["score"] is None  # NaN -> None
    assert records[0]["name"] == "a"
    assert records[0]["when"] == "2026-01-01 00:00:00"

    # Row 1: flag=pd.NA, name=None, when=NaT, score=1.5
    assert records[1]["id"] == 2
    assert records[1]["flag"] is None  # pd.NA -> None
    assert records[1]["score"] == 1.5
    assert records[1]["name"] is None
    assert records[1]["when"] is None  # NaT -> None

    # Row 2: id=pd.NA, score=None
    assert records[2]["id"] is None  # pd.NA -> None
    assert records[2]["flag"] is False
    assert records[2]["score"] is None
    assert records[2]["name"] == "c"
    assert records[2]["when"] == "2026-01-03 00:00:00"


def test_iter_dataframe_rest_records_is_lazy_generator() -> None:
    """Verifica architetturale: iter_dataframe_rest_records ritorna
    un Iterator, non una collection materializzata. Pre-fix
    `dataframe_to_rest_records` allocava `tuple(...)` upfront."""
    import inspect
    import pandas as pd
    from app.core.dataframes.tabular import iter_dataframe_rest_records

    df = pd.DataFrame([{"id": 1}, {"id": 2}])
    result = iter_dataframe_rest_records(df)

    # Iterator/generator, non list/tuple
    assert hasattr(result, "__next__")
    assert not isinstance(result, (list, tuple))

    # Architectural: il pattern `to_dict(orient="records")`
    # materializza tutto in lista upfront -> vietato nel body
    # (la docstring puo' citarlo come riferimento storico al pre-fix).
    import ast
    src = inspect.getsource(iter_dataframe_rest_records)
    tree = ast.parse(src)
    func_def = tree.body[0]
    assert isinstance(func_def, ast.FunctionDef)
    # Strip docstring
    body_nodes = (
        func_def.body[1:]
        if (func_def.body and isinstance(func_def.body[0], ast.Expr)
            and isinstance(func_def.body[0].value, ast.Constant))
        else func_def.body
    )
    body_src = "\n".join(ast.unparse(n) for n in body_nodes)
    assert "to_dict" not in body_src, (
        "regressione lazy: iter_dataframe_rest_records non deve "
        "usare df.to_dict nel body (materializza upfront), deve "
        "iterare via itertuples"
    )
    assert "itertuples" in body_src


def test_tabular_business_helpers_preserve_dataframe_semantics() -> None:
    import pandas as pd
    from app.core.dataframes.tabular import (
        dataframe_records,
        dataframe_first_column,
        dataframe_first_row_dict,
        dataframe_row_by_index,
        dataframe_row_dict,
        duplicate_row_count,
        drop_duplicate_columns,
        drop_duplicate_rows_reset_index,
        estimate_merge_output_rows,
        float_series_non_null_whole_numbers,
        group_aggregate_reset,
        group_cumcount_series,
        group_named_aggregate_merge,
        group_size_reset,
        iter_dataframe_indexed_rows,
        iter_grouped_dataframes,
        matching_row_indices,
        mixed_numeric_string_object_columns,
        non_null_head_values,
        null_count_for_columns,
        row_indices_where_column_in,
        sample_dataframe,
        sample_rows_reset_index,
        select_and_rename_columns,
        set_cell_value,
        set_values_where_mask,
        sort_rows_reset_index,
        series_null_ratio,
        stringify_columns_preserving_nulls,
        unique_non_null_column_values,
        unique_rows_for_columns,
        window_function_values,
    )

    df = pd.DataFrame(
        [
            {"source_id": 1, "name": "A", "optional": None},
            {"source_id": 2, "name": "B", "optional": "x"},
        ],
        index=[10, 20],
    )

    assert null_count_for_columns(df, ["optional", "missing"]) == 1
    assert duplicate_row_count(df, ["name"]) == 0
    assert unique_non_null_column_values(df, "optional") == ["x"]
    assert row_indices_where_column_in(df, "name", {"B"}) == [20]
    assert matching_row_indices(df, {"name": "B", "optional": "x"}) == [20]
    assert dataframe_first_row_dict(df) == {
        "source_id": 1,
        "name": "A",
        "optional": None,
    }
    assert dataframe_first_column(df).tolist() == [1, 2]
    assert dataframe_row_by_index(df, 10)["name"] == "A"
    assert [(idx, row["name"]) for idx, row in iter_dataframe_indexed_rows(df)] == [
        (10, "A"),
        (20, "B"),
    ]
    assert unique_rows_for_columns(df, ["name"]).to_dict(orient="records") == [
        {"name": "A"},
        {"name": "B"},
    ]
    assert group_size_reset(df, ["name"]).to_dict(orient="records") == [
        {"name": "A", "count": 1},
        {"name": "B", "count": 1},
    ]
    assert group_aggregate_reset(
        df,
        ["name"],
        {"source_id": "sum"},
    ).to_dict(orient="records") == [
        {"name": "A", "source_id": 1},
        {"name": "B", "source_id": 2},
    ]
    merged_stats = group_named_aggregate_merge(
        df,
        "name",
        {"sum_id": ("source_id", "sum")},
    )
    assert merged_stats["sum_id"].tolist() == [1, 2]
    assert [(name, len(group)) for name, group in iter_grouped_dataframes(df, "name")] == [
        ("A", 1),
        ("B", 1),
    ]
    assert group_cumcount_series(df, "name", 1).tolist() == [1, 1]
    assert drop_duplicate_rows_reset_index(df, subset=["name"]).index.tolist() == [0, 1]
    duplicate_cols = pd.DataFrame([[1, 2]], columns=["x", "x"])
    assert drop_duplicate_columns(duplicate_cols).columns.tolist() == ["x"]
    assert sort_rows_reset_index(df, by="source_id", ascending=False)["source_id"].tolist() == [2, 1]
    assert estimate_merge_output_rows(df, df, ["name"], "inner") == 2
    assert window_function_values(df, "source_id", "cumsum").tolist() == [1, 3]
    assert float_series_non_null_whole_numbers(pd.Series([1.0, None, 2.0])) is True
    assert sample_rows_reset_index(df, n=1, random_state=42).index.tolist() == [0]
    assert series_null_ratio(df["optional"]) == 0.5
    assert non_null_head_values(df["optional"], 2) == ["x"]
    assert dataframe_row_dict(df, 20) == {
        "source_id": 2,
        "name": "B",
        "optional": "x",
    }
    assert dataframe_records(sample_dataframe(df, n=1, random_state=42)) == [
        {"source_id": 2, "name": "B", "optional": "x"}
    ]
    selected = select_and_rename_columns(
        df,
        ["source_id", "missing"],
        rename_map={"source_id": "id", "missing": "ignored"},
    )
    assert selected.to_dict(orient="records") == [{"id": 1}, {"id": 2}]
    set_cell_value(selected, 10, "id", 99)
    assert selected.loc[10, "id"] == 99
    set_values_where_mask(selected, selected["id"] == 2, "id", 22)
    assert selected.loc[20, "id"] == 22

    mixed = pd.DataFrame({"mixed": [1, "2", None], "plain": [1, 2, 3]})
    assert mixed_numeric_string_object_columns(mixed) == ["mixed"]
    stringify_columns_preserving_nulls(mixed, ["mixed"])
    assert mixed["mixed"].tolist()[:2] == ["1", "2"]
    assert pd.isna(mixed["mixed"].tolist()[2]) or mixed["mixed"].tolist()[2] is None


def test_files_dataframe_io_reexports_frame_io_boundary() -> None:
    from app.core.dataframes import frame_io
    from app.core.files import dataframe_io

    assert dataframe_io.DefaultFrameFileIO is frame_io.DefaultFrameFileIO
    assert dataframe_io.find_latest_output_file is frame_io.find_latest_output_file
    assert dataframe_io.load_dataframe_from_file is frame_io.load_dataframe_from_file
    assert dataframe_io.save_dataframe_to_bytes is frame_io.save_dataframe_to_bytes


def test_files_geospatial_io_reexports_dataframe_boundary() -> None:
    from app.core.dataframes import geospatial_io as dataframe_geospatial
    from app.core.files import geospatial_io as files_geospatial

    assert (
        files_geospatial.geospatial_dataframe_to_bytes
        is dataframe_geospatial.geospatial_dataframe_to_bytes
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
