"""
Behavioral coverage for app/core/manipola/transforms/filtering.py

Exercises the two public ops (op_filter, op_conditional) plus the private
mask evaluator (_evaluate_condition) across all significant operator
branches, type-coercion paths, empty/None handling and the fail-closed
error contract. Inputs are minimal inline pandas DataFrames (no DB/HTTP).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.manipola.transforms.filtering import (
    _evaluate_condition,
    op_conditional,
    op_filter,
)


pytestmark = [pytest.mark.unit, pytest.mark.manipola]


# ---------------------------------------------------------------------------
# op_filter — happy paths per operator
# ---------------------------------------------------------------------------


def test_op_filter_eq_numeric():
    df = pd.DataFrame({"a": [1, 2, 2, 3]})
    out = op_filter(df, "a", "==", 2)
    assert list(out["a"]) == [2, 2]


def test_op_filter_ne_numeric():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_filter(df, "a", "!=", 2)
    assert list(out["a"]) == [1, 3]


def test_op_filter_gt_with_string_value_autoconverts():
    """Numeric column + string value '2' is coerced to float before compare."""
    df = pd.DataFrame({"a": [1, 2, 3, 4]})
    out = op_filter(df, "a", ">", "2")
    assert list(out["a"]) == [3, 4]


def test_op_filter_ge():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_filter(df, "a", ">=", 2)
    assert list(out["a"]) == [2, 3]


def test_op_filter_lt():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_filter(df, "a", "<", 3)
    assert list(out["a"]) == [1, 2]


def test_op_filter_le():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_filter(df, "a", "<=", 2)
    assert list(out["a"]) == [1, 2]


def test_op_filter_float_value_with_dot():
    """Value containing '.' goes through the float() branch explicitly."""
    df = pd.DataFrame({"a": [1.0, 2.5, 3.0]})
    out = op_filter(df, "a", ">", "2.0")
    assert list(out["a"]) == [2.5, 3.0]


def test_op_filter_contains_case_insensitive_literal():
    df = pd.DataFrame({"name": ["Apple", "banana", "Pineapple", "kiwi"]})
    out = op_filter(df, "name", "contains", "apple")
    assert list(out["name"]) == ["Apple", "Pineapple"]


def test_op_filter_contains_treats_value_as_literal_not_regex():
    """regex=False: a regex metachar matches only literally (anti-ReDoS)."""
    df = pd.DataFrame({"code": ["a.b", "axb", "a.c"]})
    out = op_filter(df, "code", "contains", "a.")
    # '.' literal -> matches 'a.b' and 'a.c', NOT 'axb'
    assert list(out["code"]) == ["a.b", "a.c"]


def test_op_filter_startswith():
    df = pd.DataFrame({"s": ["foobar", "barfoo", "fooqux"]})
    out = op_filter(df, "s", "startswith", "foo")
    assert list(out["s"]) == ["foobar", "fooqux"]


def test_op_filter_endswith():
    df = pd.DataFrame({"s": ["foobar", "qux", "raconbar"]})
    out = op_filter(df, "s", "endswith", "bar")
    assert list(out["s"]) == ["foobar", "raconbar"]


def test_op_filter_isnull():
    df = pd.DataFrame({"a": [1, None, 3, None]})
    out = op_filter(df, "a", "isnull", None)
    assert len(out) == 2
    assert out["a"].isnull().all()


def test_op_filter_notnull():
    df = pd.DataFrame({"a": [1, None, 3]})
    out = op_filter(df, "a", "notnull", None)
    assert list(out["a"]) == [1.0, 3.0]


def test_op_filter_resets_index():
    df = pd.DataFrame({"a": [10, 20, 30, 40]})
    out = op_filter(df, "a", ">", 15)
    assert list(out.index) == [0, 1, 2]


# ---------------------------------------------------------------------------
# op_filter — branches: unknown column, unknown operator, errors
# ---------------------------------------------------------------------------


def test_op_filter_missing_column_raises():
    df = pd.DataFrame({"a": [1, 2]})
    with pytest.raises(ValueError) as exc:
        op_filter(df, "missing", "==", 1)
    msg = str(exc.value)
    assert "missing" in msg
    assert "non trovata" in msg
    # available columns surfaced for diagnostics
    assert "'a'" in msg


def test_op_filter_unknown_operator_returns_df_unchanged():
    df = pd.DataFrame({"a": [1, 2, 3]})
    out = op_filter(df, "a", "<>", 2)
    # unsupported operator -> logged warning, original df returned
    assert out is df
    assert list(out["a"]) == [1, 2, 3]


def test_op_filter_incompatible_compare_raises_safe_value_error(monkeypatch):
    """Runtime pandas error inside operator block -> fail-closed ValueError
    with a safe, user-facing message (no raw exception leak)."""
    def boom(self, other):
        raise TypeError("simulated incompatible compare")

    monkeypatch.setattr(pd.Series, "__gt__", boom)
    df = pd.DataFrame({"a": [1, 2, 3]})

    with pytest.raises(ValueError) as exc:
        op_filter(df, "a", ">", 2)
    msg = str(exc.value)
    assert "Filter" in msg
    assert "a" in msg
    assert "Verificare tipo e sintassi" in msg
    assert isinstance(exc.value.__cause__, TypeError)


def test_op_filter_non_numeric_value_on_numeric_column_logs_and_continues():
    """value that cannot convert to number stays a string; comparison with a
    numeric column then raises -> fail-closed ValueError (covers warning +
    error path together)."""
    df = pd.DataFrame({"a": [1, 2, 3]})
    with pytest.raises(ValueError):
        op_filter(df, "a", ">", "not-a-number")


def test_op_filter_eq_on_string_column_no_coercion():
    """Non-numeric column: value is NOT coerced, compared as-is."""
    df = pd.DataFrame({"s": ["1", "2", "3"]})
    out = op_filter(df, "s", "==", "2")
    assert list(out["s"]) == ["2"]


# ---------------------------------------------------------------------------
# op_conditional — happy path / case-when
# ---------------------------------------------------------------------------


def test_op_conditional_basic_string_results():
    df = pd.DataFrame({"score": [10, 50, 90]})
    conditions = [
        {"operator": ">=", "value": "80", "result": "high"},
        {"operator": ">=", "value": "40", "result": "mid"},
    ]
    out = op_conditional(
        df, "score", conditions, default_value="low", output_column="band"
    )
    assert list(out["band"]) == ["low", "mid", "high"]


def test_op_conditional_first_condition_has_priority():
    """Conditions applied in reverse so earlier conditions win on overlap."""
    df = pd.DataFrame({"x": [100]})
    conditions = [
        {"operator": ">=", "value": "50", "result": "A"},  # wins
        {"operator": ">=", "value": "10", "result": "B"},
    ]
    out = op_conditional(df, "x", conditions, default_value="none", output_column="r")
    assert list(out["r"]) == ["A"]


def test_op_conditional_default_when_no_match():
    df = pd.DataFrame({"x": [1, 2, 3]})
    conditions = [{"operator": ">", "value": "100", "result": "big"}]
    out = op_conditional(df, "x", conditions, default_value="small", output_column="r")
    assert list(out["r"]) == ["small", "small", "small"]


def test_op_conditional_numeric_results_autoconvert_to_numeric():
    """When every result + default is numeric, output column becomes numeric."""
    df = pd.DataFrame({"x": [5, 15]})
    conditions = [{"operator": ">", "value": "10", "result": "1"}]
    out = op_conditional(df, "x", conditions, default_value="0", output_column="flag")
    assert pd.api.types.is_numeric_dtype(out["flag"])
    assert out["flag"].tolist() == [0.0, 1.0]


def test_op_conditional_mixed_results_stay_string():
    df = pd.DataFrame({"x": [5, 15]})
    conditions = [{"operator": ">", "value": "10", "result": "yes"}]
    out = op_conditional(df, "x", conditions, default_value="0", output_column="flag")
    assert not pd.api.types.is_numeric_dtype(out["flag"])
    assert out["flag"].tolist() == ["0", "yes"]


def test_op_conditional_between_operator():
    df = pd.DataFrame({"x": [5, 15, 25]})
    conditions = [{"operator": "between", "value": "10,20", "result": "mid"}]
    out = op_conditional(df, "x", conditions, default_value="out", output_column="r")
    assert list(out["r"]) == ["out", "mid", "out"]


def test_op_conditional_comma_decimal_result_triggers_numeric_dtype_but_value_nan():
    """The all_numeric detector accepts '3,5' (it does replace(',', '.')), so
    the output column is coerced to numeric. But the stored result value is the
    raw '3,5' string, and coerce_numeric_series does NOT comma-convert, so that
    cell becomes NaN. This documents the real (slightly surprising) behavior."""
    df = pd.DataFrame({"x": [1, 2]})
    conditions = [{"operator": ">", "value": "1", "result": "3,5"}]
    out = op_conditional(df, "x", conditions, default_value="0", output_column="r")
    assert pd.api.types.is_numeric_dtype(out["r"])
    assert out["r"].iloc[0] == 0.0
    assert pd.isna(out["r"].iloc[1])


def test_op_conditional_empty_result_treated_as_numeric_compatible():
    """Empty/None result values do not block numeric auto-detect (become NaN)."""
    df = pd.DataFrame({"x": [1, 20]})
    conditions = [{"operator": ">", "value": "10", "result": "5"}]
    out = op_conditional(df, "x", conditions, default_value="", output_column="r")
    assert pd.api.types.is_numeric_dtype(out["r"])
    assert out["r"].iloc[1] == 5.0
    assert pd.isna(out["r"].iloc[0])


def test_op_conditional_bad_condition_is_skipped(monkeypatch):
    """A condition whose evaluation raises is logged and skipped; the rest of
    the conditions still apply."""
    df = pd.DataFrame({"x": [1, 2, 3]})
    conditions = [
        {"operator": ">", "value": "1", "result": "ok"},
        {"operator": "BOGUS_RAISE", "value": "x", "result": "never"},
    ]

    import app.core.manipola.transforms.filtering as mod
    real_eval = mod._evaluate_condition

    def maybe_raise(col_values, operator, value):
        if operator == "BOGUS_RAISE":
            raise RuntimeError("simulated condition failure")
        return real_eval(col_values, operator, value)

    monkeypatch.setattr(mod, "_evaluate_condition", maybe_raise)

    out = op_conditional(df, "x", conditions, default_value="d", output_column="r")
    # bad condition skipped, good '>' applied
    assert list(out["r"]) == ["d", "ok", "ok"]


# ---------------------------------------------------------------------------
# op_conditional — guard branches (return df unchanged)
# ---------------------------------------------------------------------------


def test_op_conditional_missing_column_returns_df():
    df = pd.DataFrame({"a": [1]})
    out = op_conditional(df, "nope", [{"operator": "==", "value": "1", "result": "x"}])
    assert out is df
    assert "result" not in out.columns


def test_op_conditional_empty_column_name_returns_df():
    df = pd.DataFrame({"a": [1]})
    out = op_conditional(df, "", [{"operator": "==", "value": "1", "result": "x"}])
    assert out is df


def test_op_conditional_no_conditions_returns_df():
    df = pd.DataFrame({"a": [1]})
    out = op_conditional(df, "a", [])
    assert out is df
    assert "result" not in out.columns


def test_op_conditional_does_not_mutate_input():
    df = pd.DataFrame({"x": [5, 15]})
    snapshot = df.copy()
    op_conditional(df, "x", [{"operator": ">", "value": "10", "result": "hi"}])
    pd.testing.assert_frame_equal(df, snapshot)


def test_op_conditional_defaults_output_column_named_result():
    df = pd.DataFrame({"x": [1, 2]})
    out = op_conditional(df, "x", [{"operator": ">", "value": "1", "result": "y"}])
    assert "result" in out.columns


# ---------------------------------------------------------------------------
# _evaluate_condition — direct branch coverage
# ---------------------------------------------------------------------------


def test_eval_isnull():
    s = pd.Series([1, None, 3])
    mask = _evaluate_condition(s, "isnull", "")
    assert mask.tolist() == [False, True, False]


def test_eval_notnull():
    s = pd.Series([1, None, 3])
    mask = _evaluate_condition(s, "notnull", "")
    assert mask.tolist() == [True, False, True]


def test_eval_eq_numeric_value():
    s = pd.Series([1, 2, 3])
    mask = _evaluate_condition(s, "==", "2")
    assert mask.tolist() == [False, True, False]


def test_eval_eq_string_value():
    s = pd.Series(["a", "b", "c"])
    mask = _evaluate_condition(s, "==", "b")
    assert mask.tolist() == [False, True, False]


def test_eval_ne_string_value():
    s = pd.Series(["a", "b"])
    mask = _evaluate_condition(s, "!=", "a")
    assert mask.tolist() == [False, True]


def test_eval_ne_numeric_value():
    s = pd.Series([1, 2, 3])
    mask = _evaluate_condition(s, "!=", "2")
    assert mask.tolist() == [True, False, True]


def test_eval_gt():
    s = pd.Series([1, 2, 3])
    mask = _evaluate_condition(s, ">", "2")
    assert mask.tolist() == [False, False, True]


def test_eval_comparison_non_numeric_value_defaults_to_zero():
    """For >,>=,<,<= a non-numeric value falls back to 0."""
    s = pd.Series([-1, 0, 1])
    mask = _evaluate_condition(s, ">", "abc")  # numeric_val -> 0
    assert mask.tolist() == [False, False, True]


def test_eval_le():
    s = pd.Series([1, 2, 3])
    mask = _evaluate_condition(s, "<=", "2")
    assert mask.tolist() == [True, True, False]


def test_eval_contains_is_case_insensitive():
    """_evaluate_condition contains uses case=False, regex=False."""
    s = pd.Series(["Alpha", "beta", "gamma"])
    mask = _evaluate_condition(s, "contains", "a")
    # 'a' matches all three case-insensitively (Alpha, beta, gamma)
    assert mask.tolist() == [True, True, True]


def test_eval_contains_literal_substring():
    s = pd.Series(["xyz", "abc", "axc"])
    mask = _evaluate_condition(s, "contains", "bc")
    assert mask.tolist() == [False, True, False]


def test_eval_startswith():
    s = pd.Series(["foo", "bar"])
    mask = _evaluate_condition(s, "startswith", "fo")
    assert mask.tolist() == [True, False]


def test_eval_endswith():
    s = pd.Series(["foo", "boo"])
    mask = _evaluate_condition(s, "endswith", "oo")
    assert mask.tolist() == [True, True]


def test_eval_between():
    s = pd.Series([5, 15, 25])
    mask = _evaluate_condition(s, "between", "10,20")
    assert mask.tolist() == [False, True, False]


def test_eval_between_malformed_returns_all_false():
    """between without a valid 'min,max' falls through to false series."""
    s = pd.Series([5, 15, 25])
    mask = _evaluate_condition(s, "between", "10")  # only one part
    assert mask.tolist() == [False, False, False]


def test_eval_unknown_operator_returns_all_false():
    s = pd.Series([1, 2, 3])
    mask = _evaluate_condition(s, "weird", "x")
    assert mask.tolist() == [False, False, False]
    assert mask.index.equals(s.index)
