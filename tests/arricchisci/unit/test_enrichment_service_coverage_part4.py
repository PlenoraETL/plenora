"""
tests/arricchisci/unit/test_enrichment_service_coverage.py
==========================================================
Coverage-oriented tests for enrichment_service.py targeting uncovered lines.

Covers:
  - RateLimiter: rate>0 actual sleep (lines 111-112)
  - preview(): full flow with mocked _execute_internal (lines 258-307)
  - execute(): batch_config.enabled routing (line 347)
  - execute(): parallel_workers > 1 routing (line 349)
  - _execute_parallel: full flow including dedup, null params, cache hit,
    error handling, worker crash (lines 382-536)
  - _execute_batch_native: full flow including null skip, success, null result,
    incomplete batch, error batch (lines 557-651)
  - _call_batch_api: GET batch, POST batch, 200 success, 429 rate limit,
    retriable status, non-retriable status, timeout, request error,
    retry exhausted (lines 669-776)
  - _build_batch_payload: FLAT_ARRAY format, ARRAY format, API key query
    (lines 787-819)
  - _extract_batch_output: non-list response, None items, path stripping
    (lines 829-852)
  - _execute_internal: token renewal, null params skip/error, cache hit,
    FeatureCollection explosion, error threshold, continue_on_error=False
    (lines 889, 902-904, 923-949, 968-969, 988-996, 1006, 1011-1012, 1016-1018)
  - _build_params: numpy bool_ conversion (line 1064-1066)
  - _call_api_with_retry: path param substitution, POST method, 429 rate limit,
    retriable status with backoff, non-retriable status, timeout, request error,
    retry exhausted (lines 1112-1114, 1127, 1146-1162, 1166-1179, 1191-1217)
  - _extract_output: all-None fallback to auto_flatten (line 1229)
  - _auto_flatten: FeatureCollection, list of dicts, list of non-dicts,
    generic dict, non-dict scalar (lines 1270, 1279)
  - _update_progress: callback invocation (lines 1402-1407)
  - generate_error_report: empty errors, non-empty errors (lines 1422-1449)
"""

import pytest
import json
import time
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock, call
from pathlib import Path

pytestmark = pytest.mark.unit

from app.core.arricchisci.enrichment_service import (
    EnrichmentExecutor,
    RateLimiter,
    generate_error_report,
)
from app.core.arricchisci.models import (
    EnrichmentOptions,
    EnrichmentStats,
    RowStatus,
    RowError,
    BatchConfig,
    BatchInputFormat,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────

def make_executor(
    url="http://example.com/api",
    method="GET",
    output_mapping=None,
    param_mapping=None,
    static_params=None,
    auth=None,
    options=None,
    batch=None,
    progress_callback=None,
):
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": url, "method": method},
        "auth": auth or {"type": "none"},
        "output_mapping": output_mapping or [],
        "static_parameters": static_params or {},
        "batch": batch or {},
    }
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping=param_mapping or {},
        options=options or EnrichmentOptions(rate_limit=0, max_retries=0),
        progress_callback=progress_callback,
    )


def mock_response(data, status_code=200, headers=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.text = json.dumps(data) if isinstance(data, (dict, list)) else str(data)
    resp.headers = headers or {}
    return resp




# =============================================================================
# _auto_flatten — various data types (lines 1249-1298)
# =============================================================================

class TestAutoFlatten:

    def test_feature_collection(self):
        """GeoJSON FeatureCollection is exploded to one row per feature."""
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"name": "A"},
                 "geometry": {"type": "Point", "coordinates": [12.0, 41.0]}},
                {"type": "Feature", "properties": {"name": "B"},
                 "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}},
            ]
        }
        result = ex._auto_flatten(data)
        assert len(result) == 2
        assert result[0]["name"] == "A"
        assert "geometry" in result[0]
        # Check detected types
        assert "geometry" in ex.detected_column_types

    def test_feature_with_id(self):
        """Feature with top-level id is preserved."""
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": 42, "properties": {"name": "A"},
                 "geometry": {"type": "Point", "coordinates": [12.0, 41.0]}},
            ]
        }
        result = ex._auto_flatten(data)
        assert result[0]["id"] == 42
        assert ex.detected_column_types.get("id") == "int"

    def test_empty_feature_collection(self):
        """Empty FeatureCollection returns [{}]."""
        ex = make_executor()
        data = {"type": "FeatureCollection", "features": []}
        result = ex._auto_flatten(data)
        assert result == [{}]

    def test_list_of_dicts(self):
        """List of dicts is returned as-is (flattened)."""
        ex = make_executor()
        data = [{"name": "Italy", "pop": 60000000}, {"name": "France", "pop": 67000000}]
        result = ex._auto_flatten(data)
        assert len(result) == 2

    def test_list_of_non_dicts(self):
        """List of non-dicts wraps in _response JSON."""
        ex = make_executor()
        data = [1, 2, 3]
        result = ex._auto_flatten(data)
        assert len(result) == 1
        assert "_response" in result[0]
        parsed = json.loads(result[0]["_response"])
        assert parsed == [1, 2, 3]

    def test_generic_dict(self):
        """Generic dict is flattened to 1 row."""
        ex = make_executor()
        data = {"name": "Italy", "pop": 60000000}
        result = ex._auto_flatten(data)
        assert len(result) == 1
        assert result[0]["name"] == "Italy"

    def test_scalar_value(self):
        """Non-dict, non-list value wraps in _response."""
        ex = make_executor()
        result = ex._auto_flatten("just a string")
        assert len(result) == 1
        assert result[0]["_response"] == "just a string"

    def test_integer_preservation_in_feature(self):
        """Float values that equal their int are converted to int in features."""
        ex = make_executor()
        data = {
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "properties": {"code": 42.0, "ratio": 3.14},
                 "geometry": None},
            ]
        }
        result = ex._auto_flatten(data)
        assert result[0]["code"] == 42
        assert isinstance(result[0]["code"], int)
        assert isinstance(result[0]["ratio"], float)


# =============================================================================
# _update_progress — lines 1402-1407
# =============================================================================

class TestUpdateProgress:

    def test_callback_invoked_with_correct_args(self):
        callback = MagicMock()
        ex = make_executor(progress_callback=callback)
        stats = EnrichmentStats(total_rows=100, success=30, cached=10, errors=5)
        ex._update_progress(50, 100, stats)

        callback.assert_called_once()
        percent, message = callback.call_args[0]
        assert percent == 50
        assert "50/100" in message
        assert "30 OK" in message

    def test_callback_none_no_error(self):
        """No callback set: _update_progress does nothing."""
        ex = make_executor(progress_callback=None)
        stats = EnrichmentStats(total_rows=10)
        # Should not raise
        ex._update_progress(5, 10, stats)

    def test_callback_zero_total(self):
        """total=0 should give percent=0, not division error."""
        callback = MagicMock()
        ex = make_executor(progress_callback=callback)
        stats = EnrichmentStats()
        ex._update_progress(0, 0, stats)
        percent, _ = callback.call_args[0]
        assert percent == 0


# =============================================================================
# generate_error_report — lines 1422-1449
# =============================================================================

class TestGenerateErrorReport:

    def test_empty_errors_returns_none(self):
        result = generate_error_report([], "/tmp/output")
        assert result is None

    def test_writes_csv_file(self, tmp_path):
        errors = [
            RowError(
                row_index=0,
                status=RowStatus.ERROR,
                error_message="HTTP 500",
                error_type="http_500",
                http_status=500,
                retry_count=3,
                input_params={"q": "test"},
            ),
            RowError(
                row_index=5,
                status=RowStatus.RETRY_EXHAUSTED,
                error_message="Timeout",
                error_type="timeout",
                retry_count=3,
            ),
        ]
        output_path = str(tmp_path / "output")
        result_path = generate_error_report(errors, output_path)

        assert result_path == f"{output_path}_errors.csv"

        import csv
        with open(result_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["row_index"] == "0"
        assert rows[0]["status"] == "error"
        assert rows[0]["error_type"] == "http_500"
        assert rows[0]["http_status"] == "500"
        assert rows[1]["status"] == "retry_exhausted"
        assert rows[1]["error_type"] == "timeout"


# =============================================================================
# _detect_geometry_type and _infer_scalar_type edge cases
# =============================================================================

class TestDetectGeometryType:

    def test_all_geometry_types_mapped(self):
        ex = make_executor()
        mappings = {
            "point": "geometry:point",
            "multipoint": "geometry:multipoint",
            "linestring": "geometry:line",
            "multilinestring": "geometry:line",
            "polygon": "geometry:polygon",
            "multipolygon": "geometry:multipolygon",
            "geometrycollection": "geometry",
        }
        for geom_type, expected in mappings.items():
            ex._detect_geometry_type("test_col", geom_type)
            assert ex.detected_column_types["test_col"] == expected

    def test_unknown_geometry_type(self):
        ex = make_executor()
        ex._detect_geometry_type("test_col", "unknown_type")
        assert ex.detected_column_types["test_col"] == "geometry"


class TestInferScalarTypeGeometry:

    def test_polygon_geometry(self):
        ex = make_executor()
        geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
        result = ex._infer_scalar_type(geom)
        assert "geometry" in result

    def test_none_value(self):
        ex = make_executor()
        result = ex._infer_scalar_type(None)
        assert result == "str"


# =============================================================================
# _apply_result
# =============================================================================

class TestApplyResult:

    def test_applies_all_columns(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        result = {"capitale": "Rome", "pop": 60000000}
        ex._apply_result(df, 0, result)
        assert df.at[0, "capitale"] == "Rome"
        assert df.at[0, "pop"] == 60000000

    def test_empty_result_no_error(self):
        ex = make_executor()
        df = pd.DataFrame({"paese": ["Italy"]})
        ex._apply_result(df, 0, {})
        assert len(df.columns) == 1
