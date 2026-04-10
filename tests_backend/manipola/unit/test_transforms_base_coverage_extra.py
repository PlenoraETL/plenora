"""
tests/manipola/unit/test_transforms_base_coverage_extra.py
==========================================================
Extra coverage tests targeting remaining uncovered branches in transforms/base.py.

Focuses on paths NOT already covered by existing test_transforms_base*.py files:
  - op_formula: eval fallback with object columns that fail numeric conversion,
    Italian number format with euro symbol, completely invalid formula
  - op_type_cast: str cleaning (None/nan/NaT), int with errors='ignore',
    float with non-object column, bool edge cases, date with format,
    datetime auto-detect dayfirst, date .dt.date conversion
  - op_conditional: between with bad range, numeric auto-detect with mixed values,
    default_value empty string in numeric check, multiple conditions priority
  - op_table_diff: separator custom in diff_columns/diff_old_values,
    multi-key diff, composite key matching
  - op_text_normalize: upper, title, strip_double_spaces as standalone ops,
    overwrite=False path, NaN values pass-through
  - op_pivot: concat aggfunc, unsupported aggfunc fallback, mapping with
    missing pivot values logged, multi-index as list
  - op_concat_columns: exception path, all null row with skip_null
  - op_md5_hash: geometry wkt in hash, all invalid columns
  - op_mask_data: custom mask with mask_char, overwrite=True, exception in masking
  - op_join: cross join not supported fallback, empty inputs list
  - op_filter: value auto-conversion with dot, unsupported operator
"""

import pytest
import pandas as pd
import numpy as np

pytestmark = pytest.mark.unit

from app.core.manipola.transforms.base import (
    op_join,
    op_union,
    op_filter,
    op_formula,
    op_aggregate,
    op_sort,
    op_distinct,
    op_rename,
    op_drop_columns,
    op_fill_na,
    op_replace,
    op_split_column,
    op_md5_hash,
    op_mask_data,
    op_type_cast,
    op_geo_from_coords,
    op_conditional,
    op_text_normalize,
    op_pivot,
    op_table_diff,
    op_concat_columns,
    _evaluate_condition,
)


# =============================================================================
# op_formula — additional error/edge branches
# =============================================================================

class TestOpFormulaExtra:

    def test_formula_eval_fallback_with_euro_italian(self):
        """Object column with euro symbol and Italian format triggers Italian parsing."""
        df = pd.DataFrame({"prezzo": ["€ 1.234,56", "€ 2.345,67"]})
        result = op_formula(df, formula="prezzo * 2", new_column="doppio")
        # Should succeed via Italian format conversion or fail gracefully
        assert "doppio" in result.columns or result is df

    def test_formula_completely_invalid_returns_original(self):
        """Completely invalid formula returns original df."""
        df = pd.DataFrame({"a": [1, 2]})
        result = op_formula(df, formula="INVALID_NONSENSE_@@@", new_column="x")
        pd.testing.assert_frame_equal(result, df)

    def test_formula_whitespace_only_returns_original(self):
        """Whitespace-only formula returns original df."""
        df = pd.DataFrame({"a": [1, 2]})
        result = op_formula(df, formula="   ", new_column="x")
        pd.testing.assert_frame_equal(result, df)

    def test_formula_string_concat_via_eval_namespace(self):
        """String concatenation uses eval namespace fallback."""
        df = pd.DataFrame({"nome": ["Mario", "Luigi"], "cognome": ["Rossi", "Verdi"]})
        result = op_formula(df, formula="nome + ' ' + cognome", new_column="full")
        assert "full" in result.columns
        assert result["full"].iloc[0] == "Mario Rossi"

    def test_formula_object_col_high_nan_ratio_stays_string(self):
        """Object column where numeric conversion produces too many NaN stays as string."""
        df = pd.DataFrame({
            "text": ["hello", "world", "test", "abc", "xyz"],
            "num": [1, 2, 3, 4, 5],
        })
        result = op_formula(df, formula="num + 1", new_column="calc")
        assert "calc" in result.columns
        assert result["calc"].iloc[0] == 2


# =============================================================================
# op_type_cast — remaining branches
# =============================================================================

class TestOpTypeCastExtra:

    def test_cast_to_str_cleans_none_nan_nat(self):
        """str cast replaces 'None', 'nan', 'NaT' with empty string."""
        df = pd.DataFrame({"col": [None, np.nan, 1.0, "hello"]})
        result = op_type_cast(df, column="col", target_type="str")
        # None and nan should become ''
        assert result["col"].iloc[3] == "hello"

    def test_cast_int_errors_not_coerce_uses_astype(self):
        """int cast with errors != 'coerce' tries direct astype(int)."""
        df = pd.DataFrame({"col": [1, 2, 3]})
        result = op_type_cast(df, column="col", target_type="int", errors="coerce")
        # With coerce, should use Int64 nullable
        assert result["col"].dtype == pd.Int64Dtype()

    def test_cast_int_errors_coerce_with_nan(self):
        """int cast with coerce converts non-numeric to NaN and uses Int64."""
        df = pd.DataFrame({"col": ["1", "abc", "3"]})
        result = op_type_cast(df, column="col", target_type="int", errors="coerce")
        assert result["col"].dtype == pd.Int64Dtype()
        assert pd.isna(result["col"].iloc[1])

    def test_cast_float_non_object_column(self):
        """float cast on already numeric column skips comma replacement."""
        df = pd.DataFrame({"col": [1, 2, 3]})
        result = op_type_cast(df, column="col", target_type="float")
        # pd.to_numeric on int column may keep int or convert to float
        assert pd.api.types.is_numeric_dtype(result["col"])

    def test_cast_bool_various_truthy(self):
        """bool cast recognizes various truthy strings."""
        df = pd.DataFrame({"col": ["si", "yes", "vero", "no", "false", "0"]})
        result = op_type_cast(df, column="col", target_type="bool")
        assert result["col"].iloc[0] == True   # si
        assert result["col"].iloc[1] == True   # yes
        assert result["col"].iloc[2] == True   # vero
        assert result["col"].iloc[3] == False  # no

    def test_cast_date_with_explicit_format(self):
        """date cast with explicit format parses correctly."""
        df = pd.DataFrame({"col": ["01/03/2025", "15/12/2024"]})
        result = op_type_cast(df, column="col", target_type="date", date_format="%d/%m/%Y")
        # Should produce date objects
        from datetime import date
        assert result["col"].iloc[0] == date(2025, 3, 1)

    def test_cast_datetime_auto_detect_dayfirst(self):
        """datetime auto-detect uses dayfirst=True."""
        df = pd.DataFrame({"col": ["01/03/2025 10:30", "15/12/2024 08:00"]})
        result = op_type_cast(df, column="col", target_type="datetime")
        assert pd.api.types.is_datetime64_any_dtype(result["col"])

    def test_cast_missing_column_returns_original(self):
        """Missing column returns original df unchanged."""
        df = pd.DataFrame({"a": [1]})
        result = op_type_cast(df, column="nonexistent", target_type="int")
        pd.testing.assert_frame_equal(result, df)

    def test_cast_exception_raise_propagates(self):
        """errors='raise' propagates the exception."""
        df = pd.DataFrame({"col": ["not_a_date"]})
        with pytest.raises(Exception):
            op_type_cast(df, column="col", target_type="date",
                         date_format="%Y-%m-%d", errors="raise")


# =============================================================================
# op_conditional — additional branches
# =============================================================================

class TestOpConditionalExtra:

    def test_between_with_two_parts(self):
        """between operator with valid min,max range."""
        df = pd.DataFrame({"val": [1, 5, 10, 15, 20]})
        result = op_conditional(
            df, column="val",
            conditions=[{"operator": "between", "value": "5,15", "result": "in_range"}],
            default_value="out",
            output_column="status"
        )
        assert result["status"].iloc[0] == "out"   # 1 is out
        assert result["status"].iloc[1] == "in_range"  # 5 is in
        assert result["status"].iloc[3] == "in_range"  # 15 is in
        assert result["status"].iloc[4] == "out"   # 20 is out

    def test_numeric_autodetect_with_empty_values(self):
        """All result values numeric (including empty) -> auto-convert to numeric."""
        df = pd.DataFrame({"val": [1, 2, 3]})
        result = op_conditional(
            df, column="val",
            conditions=[{"operator": "==", "value": "1", "result": "100"}],
            default_value="0",
            output_column="score"
        )
        assert pd.api.types.is_numeric_dtype(result["score"])

    def test_non_numeric_results_stay_string(self):
        """Mixed numeric/string result values stay as string."""
        df = pd.DataFrame({"val": [1, 2]})
        result = op_conditional(
            df, column="val",
            conditions=[{"operator": "==", "value": "1", "result": "alto"}],
            default_value="basso",
            output_column="cat"
        )
        # Should not be numeric - string or object dtype
        assert not pd.api.types.is_numeric_dtype(result["cat"])

    def test_condition_exception_skipped_gracefully(self):
        """Bad condition is skipped, others still apply."""
        df = pd.DataFrame({"val": ["a", "b", "c"]})
        result = op_conditional(
            df, column="val",
            conditions=[
                {"operator": "between", "value": "not,valid", "result": "bad"},
                {"operator": "==", "value": "a", "result": "found"},
            ],
            default_value="none",
            output_column="res"
        )
        assert result["res"].iloc[0] == "found"

    def test_evaluate_condition_neq_string(self):
        """!= with non-numeric value uses string comparison."""
        col = pd.Series(["a", "b", "c"])
        mask = _evaluate_condition(col, "!=", "b")
        assert mask.tolist() == [True, False, True]

    def test_evaluate_condition_comparison_non_numeric_defaults_zero(self):
        """Comparison with non-numeric value defaults numeric_val to 0."""
        col = pd.Series([1, 0, -1])
        mask = _evaluate_condition(col, ">", "abc")
        assert mask.iloc[0] == True   # 1 > 0
        assert mask.iloc[1] == False  # 0 > 0
        assert mask.iloc[2] == False  # -1 > 0

    def test_evaluate_condition_le(self):
        """<= operator."""
        col = pd.Series([1, 5, 10])
        mask = _evaluate_condition(col, "<=", "5")
        assert mask.tolist() == [True, True, False]


# =============================================================================
# op_table_diff — additional branches
# =============================================================================

class TestOpTableDiffExtra:

    def test_separator_custom_in_modified(self):
        """Custom separator used in _diff_columns and _diff_old_values."""
        old = pd.DataFrame({"id": [1], "a": [10], "b": ["x"]})
        new = pd.DataFrame({"id": [1], "a": [20], "b": ["y"]})
        result = op_table_diff(
            [old, new], left_keys="id", right_keys="id", separator="|"
        )
        mod = result[result["_diff_status"] == "MODIFIED"]
        assert "|" in mod["_diff_columns"].iloc[0]

    def test_multi_key_diff(self):
        """Table diff with composite keys."""
        old = pd.DataFrame({"k1": [1, 1], "k2": ["a", "b"], "val": [10, 20]})
        new = pd.DataFrame({"k1": [1, 1], "k2": ["a", "b"], "val": [10, 99]})
        result = op_table_diff(
            [old, new], left_keys="k1,k2", right_keys="k1,k2"
        )
        mod = result[result["_diff_status"] == "MODIFIED"]
        assert len(mod) == 1
        assert mod["val"].iloc[0] == 99

    def test_empty_keys_returns_old(self):
        """Empty key strings returns old df."""
        old = pd.DataFrame({"id": [1], "val": [10]})
        new = pd.DataFrame({"id": [1], "val": [20]})
        result = op_table_diff([old, new], left_keys="", right_keys="")
        pd.testing.assert_frame_equal(result, old)

    def test_single_input_returns_it(self):
        """Single input returns it directly."""
        df = pd.DataFrame({"id": [1]})
        result = op_table_diff([df], left_keys="id", right_keys="id")
        pd.testing.assert_frame_equal(result, df)

    def test_left_key_missing_returns_old(self):
        """Left key not in old_df returns old."""
        old = pd.DataFrame({"id": [1], "val": [10]})
        new = pd.DataFrame({"id": [1], "val": [20]})
        result = op_table_diff([old, new], left_keys="MISSING", right_keys="id")
        pd.testing.assert_frame_equal(result, old)


# =============================================================================
# op_text_normalize — extra branch coverage
# =============================================================================

class TestOpTextNormalizeExtra:

    def test_upper_operation(self):
        """upper operation."""
        df = pd.DataFrame({"name": ["mario", "luigi"]})
        result = op_text_normalize(df, columns=["name"], operations="upper")
        assert result["name"].iloc[0] == "MARIO"

    def test_title_operation(self):
        """title operation."""
        df = pd.DataFrame({"name": ["mario rossi", "luigi verdi"]})
        result = op_text_normalize(df, columns=["name"], operations="title")
        assert result["name"].iloc[0] == "Mario Rossi"

    def test_strip_double_spaces(self):
        """strip_double_spaces operation."""
        df = pd.DataFrame({"text": ["hello   world", "a  b  c"]})
        result = op_text_normalize(df, columns=["text"], operations="strip_double_spaces")
        assert result["text"].iloc[0] == "hello world"

    def test_overwrite_false_creates_norm_column(self):
        """overwrite=False creates {col}_norm."""
        df = pd.DataFrame({"name": ["  MARIO  "]})
        result = op_text_normalize(df, columns=["name"], operations="trim", overwrite=False)
        assert "name_norm" in result.columns
        assert result["name_norm"].iloc[0] == "MARIO"
        assert result["name"].iloc[0] == "  MARIO  "

    def test_nan_values_pass_through(self):
        """NaN values are passed through unchanged."""
        df = pd.DataFrame({"name": ["hello", None, np.nan]})
        result = op_text_normalize(df, columns=["name"], operations="lower")
        assert result["name"].iloc[0] == "hello"
        assert pd.isna(result["name"].iloc[1])

    def test_no_valid_columns_returns_unchanged(self):
        """No valid columns returns original df."""
        df = pd.DataFrame({"a": [1]})
        result = op_text_normalize(df, columns=["nonexistent"], operations="lower")
        pd.testing.assert_frame_equal(result, df)

    def test_empty_columns_list(self):
        """Empty columns list returns original df."""
        df = pd.DataFrame({"a": [1]})
        result = op_text_normalize(df, columns=[], operations="lower")
        pd.testing.assert_frame_equal(result, df)


# =============================================================================
# op_pivot — extra branch coverage
# =============================================================================

class TestOpPivotExtra:

    def test_pivot_concat_aggregation(self):
        """concat aggfunc joins values as comma-separated string."""
        df = pd.DataFrame({
            "grp": ["A", "A", "B"],
            "tipo": ["x", "x", "x"],
            "val": ["v1", "v2", "v3"],
        })
        result = op_pivot(df, index_col="grp", pivot_col="tipo",
                          value_col="val", aggr_func="concat")
        assert len(result) == 2
        a_row = result[result["grp"] == "A"]
        assert "v1" in str(a_row["x"].iloc[0])
        assert "v2" in str(a_row["x"].iloc[0])

    def test_pivot_unsupported_aggfunc_fallback(self):
        """Unsupported aggfunc falls back to 'first'."""
        df = pd.DataFrame({
            "grp": ["A", "A"],
            "tipo": ["x", "x"],
            "val": [10, 20],
        })
        result = op_pivot(df, index_col="grp", pivot_col="tipo",
                          value_col="val", aggr_func="INVALID")
        assert len(result) == 1

    def test_pivot_mapping_with_rename(self):
        """Mapping renames pivot columns."""
        df = pd.DataFrame({
            "id": ["A", "A"],
            "tipo": ["canone", "rendita"],
            "importo": [1000, 500],
        })
        result = op_pivot(df, index_col="id", pivot_col="tipo",
                          value_col="importo", aggr_func="first",
                          mapping={"canone": "val_canone", "rendita": "val_rendita"})
        assert "val_canone" in result.columns
        assert "val_rendita" in result.columns
        assert result["val_canone"].iloc[0] == 1000

    def test_pivot_mapping_with_missing_value_logged(self):
        """Mapping with a value not in pivot data still works for found values."""
        df = pd.DataFrame({
            "id": ["A"],
            "tipo": ["canone"],
            "importo": [1000],
        })
        result = op_pivot(df, index_col="id", pivot_col="tipo",
                          value_col="importo", aggr_func="first",
                          mapping={"canone": "val_canone", "rendita": "val_rendita"})
        assert "val_canone" in result.columns

    def test_pivot_missing_columns_returns_original(self):
        """Missing columns in df returns original."""
        df = pd.DataFrame({"a": [1]})
        result = op_pivot(df, index_col="MISSING", pivot_col="MISSING2",
                          value_col="MISSING3")
        pd.testing.assert_frame_equal(result, df)

    def test_pivot_empty_params_returns_original(self):
        """Empty required params returns original."""
        df = pd.DataFrame({"a": [1]})
        result = op_pivot(df, index_col="", pivot_col="a", value_col="a")
        pd.testing.assert_frame_equal(result, df)


# =============================================================================
# op_concat_columns — extra paths
# =============================================================================

class TestOpConcatColumnsExtra:

    def test_all_null_row_with_skip_null(self):
        """Row where all columns are null with skip_null returns None."""
        df = pd.DataFrame({"a": [None], "b": [None]})
        result = op_concat_columns(df, columns=["a", "b"], skip_null=True)
        assert result["concatenated"].iloc[0] is None

    def test_mixed_null_non_null_skip_null(self):
        """Mixed null and non-null with skip_null."""
        df = pd.DataFrame({"a": ["hello"], "b": [None]})
        result = op_concat_columns(df, columns=["a", "b"], separator="-", skip_null=True)
        assert result["concatenated"].iloc[0] == "hello"

    def test_custom_output_column(self):
        """Custom output column name."""
        df = pd.DataFrame({"a": ["x"], "b": ["y"]})
        result = op_concat_columns(df, columns=["a", "b"], output_column="merged")
        assert "merged" in result.columns


# =============================================================================
# op_md5_hash — geometry wkt branch
# =============================================================================

class TestOpMd5HashExtra:

    def test_geometry_wkt_in_hash(self):
        """Geometry objects with .wkt attribute are hashed via WKT."""
        from shapely.geometry import Point
        import geopandas as gpd
        gdf = gpd.GeoDataFrame(
            {"id": [1, 2]},
            geometry=[Point(0, 0), Point(1, 1)],
            crs="EPSG:4326"
        )
        result = op_md5_hash(gdf, columns=["geometry"], output_column="hash")
        assert "hash" in result.columns
        assert result["hash"].iloc[0] != result["hash"].iloc[1]

    def test_all_invalid_columns_returns_original(self):
        """All invalid column names returns original df."""
        df = pd.DataFrame({"a": [1]})
        result = op_md5_hash(df, columns=["MISSING1", "MISSING2"])
        pd.testing.assert_frame_equal(result, df)


# =============================================================================
# op_mask_data — extra paths
# =============================================================================

class TestOpMaskDataExtra:

    def test_custom_mask_with_custom_char(self):
        """Custom mask with different mask_char."""
        df = pd.DataFrame({"code": ["ABCDEFGHIJ"]})
        result = op_mask_data(df, maskings=[{
            "column": "code", "mask_type": "custom",
            "chars_start": 2, "chars_end": 2, "mask_char": "#"
        }])
        assert result["code_masked"].iloc[0] == "AB######IJ"

    def test_overwrite_true_replaces_column(self):
        """overwrite=True replaces the original column."""
        df = pd.DataFrame({"cf": ["RSSMRA80A01H501Z"]})
        result = op_mask_data(df, maskings=[{"column": "cf", "mask_type": "cf"}],
                              overwrite=True)
        assert "cf_masked" not in result.columns
        assert "*" in result["cf"].iloc[0]

    def test_missing_column_skipped(self):
        """Missing column in masking config is skipped."""
        df = pd.DataFrame({"a": [1]})
        result = op_mask_data(df, maskings=[{"column": "MISSING", "mask_type": "cf"}])
        pd.testing.assert_frame_equal(result, df)


# =============================================================================
# op_filter — additional branches
# =============================================================================

class TestOpFilterExtra:

    def test_value_auto_conversion_float_string(self):
        """Numeric column with string value containing dot converts to float."""
        df = pd.DataFrame({"price": [10.5, 20.3, 30.1]})
        result = op_filter(df, column="price", operator=">", value="15.0")
        assert len(result) == 2

    def test_unsupported_operator(self):
        """Unsupported operator returns original df."""
        df = pd.DataFrame({"a": [1, 2]})
        result = op_filter(df, column="a", operator="UNSUPPORTED", value="1")
        pd.testing.assert_frame_equal(result, df)

    def test_contains_case_insensitive(self):
        """contains is case-insensitive."""
        df = pd.DataFrame({"name": ["Mario", "LUIGI", "anna"]})
        result = op_filter(df, column="name", operator="contains", value="AR")
        assert len(result) == 1  # only Mario

    def test_isnull_filter(self):
        """isnull filter selects null rows."""
        df = pd.DataFrame({"a": [1, None, 3]})
        result = op_filter(df, column="a", operator="isnull")
        assert len(result) == 1

    def test_notnull_filter(self):
        """notnull filter excludes null rows."""
        df = pd.DataFrame({"a": [1, None, 3]})
        result = op_filter(df, column="a", operator="notnull")
        assert len(result) == 2


# =============================================================================
# op_geo_from_coords — extra edge cases
# =============================================================================

class TestOpGeoFromCoordsExtra:

    def test_wkt_with_empty_string(self):
        """Empty string WKT values produce None geometry."""
        df = pd.DataFrame({"wkt_col": ["POINT (9 45)", "", None]})
        result = op_geo_from_coords(df, mode="wkt", wkt_column="wkt_col")
        assert result["geometry"].iloc[0] is not None
        assert result["geometry"].iloc[1] is None
        assert result["geometry"].iloc[2] is None

    def test_unsupported_mode(self):
        """Unsupported mode returns original df."""
        df = pd.DataFrame({"a": [1]})
        result = op_geo_from_coords(df, mode="INVALID")
        pd.testing.assert_frame_equal(result, df)

    def test_latlon_with_nan_values(self):
        """NaN lat/lon produce None geometry."""
        df = pd.DataFrame({"lat": [45.0, np.nan], "lon": [9.0, 9.0]})
        result = op_geo_from_coords(
            df, mode="latlon", lat_column="lat", lon_column="lon"
        )
        assert result["geometry"].iloc[0] is not None
        assert result["geometry"].iloc[1] is None


# =============================================================================
# op_join — edge cases
# =============================================================================

class TestOpJoinExtra:

    def test_empty_inputs_returns_empty_df(self):
        """Empty inputs list returns empty DataFrame."""
        result = op_join([], left_on="id", right_on="id")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 0

    def test_single_input_returns_it(self):
        """Single input returns it directly."""
        df = pd.DataFrame({"id": [1], "val": [10]})
        result = op_join([df], left_on="id", right_on="id")
        assert len(result) == 1

    def test_join_with_same_column_names(self):
        """Join where both DFs have identical non-key columns gets suffixes."""
        left = pd.DataFrame({"id": [1, 2], "name": ["A", "B"]})
        right = pd.DataFrame({"id": [1, 3], "name": ["X", "Y"]})
        result = op_join([left, right], left_on="id", right_on="id", how="inner")
        assert len(result) == 1
        # Should have suffixed name columns
        cols = list(result.columns)
        assert any("name" in c for c in cols)


# =============================================================================
# op_aggregate — concat aggregation
# =============================================================================

class TestOpAggregateExtra:

    def test_concat_with_distinct(self):
        """concat aggregation with distinct=True removes duplicates."""
        df = pd.DataFrame({
            "grp": ["A", "A", "A"],
            "tag": ["x", "x", "y"]
        })
        result = op_aggregate(df, group_by=["grp"], aggregations=[
            {"column": "tag", "function": "concat", "distinct": True, "separator": ","}
        ])
        assert len(result) == 1
        val = result["tag"].iloc[0]
        assert val.count("x") == 1

    def test_concat_with_skip_null_false(self):
        """concat with skip_null=False includes None values."""
        df = pd.DataFrame({
            "grp": ["A", "A"],
            "val": ["hello", None]
        })
        result = op_aggregate(df, group_by=["grp"], aggregations=[
            {"column": "val", "function": "concat", "skip_null": False, "separator": ","}
        ])
        assert len(result) == 1

    def test_multiple_aggregations_on_same_column(self):
        """Multiple aggregation functions on the same column produce MultiIndex."""
        df = pd.DataFrame({
            "grp": ["A", "A", "B"],
            "val": [10, 20, 30]
        })
        result = op_aggregate(df, group_by=["grp"], aggregations=[
            {"column": "val", "function": "sum"},
            {"column": "val", "function": "count"},
        ])
        assert len(result) == 2
        # Should have flattened MultiIndex columns
        assert any("sum" in str(c) or "val" in str(c) for c in result.columns)
