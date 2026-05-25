"""
Contract test per `app.core.output_contract.service::
ensure_dataframe_matches_contract` con type drift detection.

Cluster bug coperto (commit cf154e57): pre-fix lo helper verificava
solo set-membership sui nomi visibili. Una colonna dichiarata
`integer` nel contratto ma scritta come `float64` (es. round-trip
parquet con NaN) passava silenziosamente al consumer cross-project,
con rischio di corruzione numerica > 2^53.

Post-fix: parametro opzionale `dataframe: Optional[pd.DataFrame]`;
quando passato, verifica anche compatibility dtype↔logical type
emettendo `logger.warning` (non raise) per back-compat.
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from app.core.output_contract.service import (
    _dtype_compatible_with_logical,
    ensure_dataframe_matches_contract,
)


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


class TestDtypeCompatibilityMatrix:
    """Compatibility matrix logical type ↔ pandas dtype."""

    @pytest.mark.parametrize("dtype", [
        "int8", "int16", "int32", "int64",
        "Int8", "Int16", "Int32", "Int64",
        "uint8", "uint16", "uint32", "uint64",
    ])
    def test_integer_logical_accepts_int_dtypes(self, dtype):
        assert _dtype_compatible_with_logical(dtype, "integer"), (
            f"{dtype} non compatibile con 'integer' — contract drift"
        )

    def test_integer_rejects_float_dtype(self):
        """Critico per evitare round-trip parquet Int64 → float64
        silente."""
        assert not _dtype_compatible_with_logical("float64", "integer"), (
            "regressione: 'float64' compatibile con 'integer' — "
            "perdita precisione su valori > 2^53 silente"
        )
        assert not _dtype_compatible_with_logical("float32", "integer")

    def test_number_logical_accepts_both_int_and_float(self):
        for dtype in ["float64", "float32", "int64", "Int64"]:
            assert _dtype_compatible_with_logical(dtype, "number")

    def test_string_logical_accepts_object_and_string(self):
        for dtype in ["object", "string", "category"]:
            assert _dtype_compatible_with_logical(dtype, "string")

    def test_boolean_logical_accepts_bool_and_object(self):
        for dtype in ["bool", "boolean", "object"]:
            assert _dtype_compatible_with_logical(dtype, "boolean")

    def test_datetime_logical_accepts_datetime64(self):
        dtype = pd.Series(pd.date_range("2026-01-01", periods=3)).dtype
        assert _dtype_compatible_with_logical(dtype, "datetime")

    def test_unknown_logical_permissive(self):
        """Logical sconosciuto → True (no false-positive warning)."""
        assert _dtype_compatible_with_logical("object", "unknown_type")


class TestEnsureDataframeMatchesContract:
    """`ensure_dataframe_matches_contract` end-to-end."""

    def test_missing_name_still_raises(self):
        """Backward compat: il check di membership resta hard-raise."""
        with pytest.raises(ValueError, match="colonne mancanti"):
            ensure_dataframe_matches_contract(
                dataframe_columns=["other"],
                node_columns=[
                    {"name": "age", "type": "integer", "visible": True}
                ],
                source_project_id="test-proj",
            )

    def test_backward_compat_no_dataframe_arg(self):
        """Chiamata senza `dataframe=` (legacy callsite) non rompe."""
        # Should not raise (column present, no type check)
        ensure_dataframe_matches_contract(
            dataframe_columns=["age"],
            node_columns=[
                {"name": "age", "type": "integer", "visible": True}
            ],
            source_project_id="test-proj",
        )

    def test_type_drift_emits_warning_no_raise(self, monkeypatch):
        """`float64` su `integer` logical → logger.warning (non raise).

        Il logger Plenora `get_logger` non propaga a `caplog` di
        default → catturiamo il warning monkey-patchando il logger
        del modulo service.
        """
        import app.core.output_contract.service as svc

        captured: list[tuple[str, tuple, dict]] = []

        class _CaptureLogger:
            def warning(self, msg, *args, **kwargs):
                captured.append((msg, args, kwargs))
            def info(self, *a, **k): pass
            def debug(self, *a, **k): pass
            def error(self, *a, **k): pass

        monkeypatch.setattr(svc, "logger", _CaptureLogger())

        df = pd.DataFrame({"age": [1.0, 2.0, 3.0]})  # float64
        ensure_dataframe_matches_contract(
            dataframe_columns=df.columns,
            node_columns=[
                {"name": "age", "type": "integer", "visible": True}
            ],
            source_project_id="test-proj-drift",
            dataframe=df,
        )
        assert captured, (
            "regressione: type drift float64 vs integer non emette "
            "warning. Il consumer cross-project non viene avvisato."
        )
        # Il warning deve menzionare project_id E la colonna
        full_msg = " ".join(
            (msg % args) if args else msg
            for msg, args, _ in captured
        )
        assert "test-proj-drift" in full_msg
        assert "age" in full_msg

    def test_compatible_types_no_warning(self, monkeypatch):
        """Int64 vs `integer` → nessun warning di drift."""
        import app.core.output_contract.service as svc

        captured: list[str] = []

        class _CaptureLogger:
            def warning(self, msg, *args, **kwargs):
                captured.append((msg % args) if args else msg)
            def info(self, *a, **k): pass
            def debug(self, *a, **k): pass
            def error(self, *a, **k): pass

        monkeypatch.setattr(svc, "logger", _CaptureLogger())

        df = pd.DataFrame({"age": pd.Series([1, 2, 3], dtype="Int64")})
        ensure_dataframe_matches_contract(
            dataframe_columns=df.columns,
            node_columns=[
                {"name": "age", "type": "integer", "visible": True}
            ],
            source_project_id="test-proj-clean",
            dataframe=df,
        )
        drift_warnings = [m for m in captured if "type drift" in m.lower()]
        assert not drift_warnings, (
            f"falso positivo: Int64 vs integer ha emesso drift "
            f"warning: {drift_warnings}"
        )

    def test_invisible_columns_not_checked(self):
        """Colonna con `visible=False` viene saltata sia da membership
        sia da type check (non-public)."""
        df = pd.DataFrame({"foo": [1.0]})
        # private_col non e' in df.columns, ma `visible=False` → skip
        ensure_dataframe_matches_contract(
            dataframe_columns=df.columns,
            node_columns=[
                {"name": "private_col", "type": {"logical": "string"}, "visible": False},
                {"name": "foo", "type": {"logical": "number"}, "visible": True},
            ],
            source_project_id="test-proj",
            dataframe=df,
        )
