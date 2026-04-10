"""
tests/carica/unit/test_conflict_checker_coverage.py
=====================================================
Unit tests for app.core.carica.conflict_checker — targeting uncovered lines:
77-110, 131-133, 151-158, 169-209, 223-281
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timezone

pytestmark = pytest.mark.unit

from app.core.carica.conflict_checker import ConflictChecker, _serialize_value
from app.core.carica.models import ConflictDetail, ConflictReport


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_engine():
    return MagicMock()


def _checker(db_type="postgres"):
    return ConflictChecker(engine=_mock_engine(), db_type=db_type)




# ===========================================================================
# _serialize_value
# ===========================================================================

class TestSerializeValue:

    def test_none_returns_none(self):
        assert _serialize_value(None) is None

    def test_nan_returns_none(self):
        assert _serialize_value(float("nan")) is None

    def test_pd_nat_returns_none(self):
        assert _serialize_value(pd.NaT) is None

    def test_numpy_int64(self):
        result = _serialize_value(np.int64(42))
        assert result == 42
        assert isinstance(result, int)

    def test_numpy_float32(self):
        result = _serialize_value(np.float32(1.5))
        assert abs(result - 1.5) < 1e-5

    def test_regular_string(self):
        assert _serialize_value("hello") == "hello"

    def test_regular_int(self):
        assert _serialize_value(7) == 7

    def test_zero_stays_zero(self):
        result = _serialize_value(0)
        assert result == 0
        assert result is not None

    def test_empty_string_stays(self):
        assert _serialize_value("") == ""


# ===========================================================================
# ConflictChecker.__init__
# ===========================================================================

class TestConflictCheckerInit:

    def test_db_type_lowered(self):
        c = ConflictChecker(engine=MagicMock(), db_type="POSTGRES")
        assert c.db_type == "postgres"

    def test_db_type_mixed_case(self):
        c = ConflictChecker(engine=MagicMock(), db_type="Oracle")
        assert c.db_type == "oracle"


# ===========================================================================
# check — full flow (lines 77-110)
# ===========================================================================

class TestCheckFullFlow:

    def test_empty_match_columns_returns_empty_report(self):
        checker = _checker()
        df = pd.DataFrame({"id": [1, 2, 3]})
        report = checker.check(df, "public", "test", match_columns=[])
        assert report.has_conflicts is False
        assert report.total_conflicts == 0
        assert report.internal_duplicates == 0
        assert report.db_conflicts == 0
        assert report.checked_at is not None

    def test_missing_match_column_returns_empty_report(self):
        checker = _checker()
        df = pd.DataFrame({"id": [1, 2]})
        report = checker.check(df, "public", "test", match_columns=["nonexistent"])
        assert report.has_conflicts is False
        assert report.total_conflicts == 0

    def test_partially_missing_match_columns(self):
        """If one of the match_columns is missing, report is empty."""
        checker = _checker()
        df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
        report = checker.check(df, "public", "test", match_columns=["id", "missing_col"])
        assert report.has_conflicts is False

    def test_no_conflicts_found(self):
        checker = _checker()
        # Mock _check_internal_duplicates and _check_db_conflicts
        checker._check_internal_duplicates = MagicMock(return_value=0)
        checker._check_db_conflicts = MagicMock(return_value=(0, []))

        df = pd.DataFrame({"id": [1, 2, 3]})
        report = checker.check(df, "public", "test", match_columns=["id"])
        assert report.has_conflicts is False
        assert report.total_conflicts == 0

    def test_with_conflicts(self):
        checker = _checker()
        checker._check_internal_duplicates = MagicMock(return_value=2)
        detail = ConflictDetail(row_index=0, match_values={"id": 1}, conflict_type="pk_duplicate")
        checker._check_db_conflicts = MagicMock(return_value=(3, [detail]))

        df = pd.DataFrame({"id": [1, 2, 3]})
        report = checker.check(df, "public", "test", match_columns=["id"])
        assert report.has_conflicts is True
        assert report.total_conflicts == 5  # 2 internal + 3 db
        assert report.internal_duplicates == 2
        assert report.db_conflicts == 3
        assert len(report.details) == 1

    def test_checked_at_is_set(self):
        checker = _checker()
        checker._check_internal_duplicates = MagicMock(return_value=0)
        checker._check_db_conflicts = MagicMock(return_value=(0, []))

        df = pd.DataFrame({"id": [1]})
        report = checker.check(df, "public", "t", match_columns=["id"])
        assert report.checked_at is not None
        # Should be parseable as ISO
        datetime.fromisoformat(report.checked_at)


# ===========================================================================
# _check_internal_duplicates (lines 131-133 on exception)
# ===========================================================================

class TestCheckInternalDuplicates:

    def test_no_duplicates(self):
        checker = _checker()
        df = pd.DataFrame({"id": [1, 2, 3]})
        result = checker._check_internal_duplicates(df, ["id"])
        assert result == 0

    def test_with_duplicates(self):
        checker = _checker()
        df = pd.DataFrame({"id": [1, 1, 2, 2, 3]})
        result = checker._check_internal_duplicates(df, ["id"])
        assert result == 2  # two excess rows (1 dup of 1, 1 dup of 2)

    def test_all_duplicates(self):
        checker = _checker()
        df = pd.DataFrame({"id": [1, 1, 1]})
        result = checker._check_internal_duplicates(df, ["id"])
        assert result == 2  # keeps first, 2 are dupes

    def test_multi_column_duplicates(self):
        checker = _checker()
        df = pd.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "x"]})
        result = checker._check_internal_duplicates(df, ["a", "b"])
        assert result == 1

    def test_exception_returns_zero(self):
        """On exception, _check_internal_duplicates returns 0 (lines 131-133)."""
        checker = _checker()
        df = MagicMock()
        df.duplicated.side_effect = RuntimeError("pandas boom")
        result = checker._check_internal_duplicates(df, ["id"])
        assert result == 0


# ===========================================================================
# _check_db_conflicts dispatch (lines 151-158)
# ===========================================================================

class TestCheckDbConflictsDispatch:

    def test_single_column_dispatches(self):
        checker = _checker()
        checker._check_single_column = MagicMock(return_value=(1, []))
        df = pd.DataFrame({"id": [1, 2]})
        count, details = checker._check_db_conflicts(df, "public", "t", ["id"])
        checker._check_single_column.assert_called_once_with(df, "public", "t", "id")
        assert count == 1

    def test_multi_column_dispatches(self):
        checker = _checker()
        checker._check_multi_column = MagicMock(return_value=(2, []))
        df = pd.DataFrame({"a": [1], "b": [2]})
        count, details = checker._check_db_conflicts(df, "public", "t", ["a", "b"])
        checker._check_multi_column.assert_called_once_with(df, "public", "t", ["a", "b"])
        assert count == 2

    def test_exception_returns_zero_empty(self):
        """On exception, returns (0, []) — lines 156-158."""
        checker = _checker()
        checker._check_single_column = MagicMock(side_effect=RuntimeError("db down"))
        df = pd.DataFrame({"id": [1]})
        count, details = checker._check_db_conflicts(df, "public", "t", ["id"])
        assert count == 0
        assert details == []


# ===========================================================================
# _check_single_column (lines 169-209)
# ===========================================================================

class TestCheckSingleColumn:

    def test_empty_values_returns_zero(self):
        """If all values in column are NaN, returns (0, [])."""
        checker = _checker()
        df = pd.DataFrame({"id": [None, None, None]})
        count, details = checker._check_single_column(df, "public", "t", "id")
        assert count == 0
        assert details == []

    def test_no_conflicts_in_db(self):
        checker = _checker()
        engine = checker.engine
        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1, 2, 3]})
        count, details = checker._check_single_column(df, "public", "t", "id")
        assert count == 0
        assert details == []

    def test_with_conflicts_in_db(self):
        checker = _checker()
        engine = checker.engine

        # Simulate DB returning conflicting values
        mock_conn = MagicMock()
        # The DB returns rows with value 1 and 2
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([(1,), (2,)])
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1, 2, 3, 1]})
        count, details = checker._check_single_column(df, "public", "t", "id")
        assert count == 2  # 2 distinct conflicting values
        # Details: rows 0, 1, 3 have conflicting ids (1 appears at 0 and 3, 2 at 1)
        assert len(details) == 3
        assert details[0].conflict_type == "pk_duplicate"
        assert details[0].match_values["id"] == 1

    def test_batch_query_exception_continues(self):
        """If a batch query fails, it continues to next batch (line 192-193)."""
        checker = _checker()
        checker.MAX_IN_BATCH = 2  # small batch to force multiple
        engine = checker.engine

        call_count = [0]

        def side_effect_connect():
            ctx = MagicMock()
            mock_conn = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                mock_conn.execute.side_effect = RuntimeError("batch fail")
            else:
                mock_result = MagicMock()
                mock_result.__iter__ = lambda self: iter([])
                mock_conn.execute.return_value = mock_result
            ctx.__enter__ = MagicMock(return_value=mock_conn)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        engine.connect = side_effect_connect

        df = pd.DataFrame({"id": [1, 2, 3, 4]})
        count, details = checker._check_single_column(df, "public", "t", "id")
        # First batch failed, second succeeded with no conflicts
        assert count == 0

    def test_max_details_capped(self):
        """Details are capped at MAX_DETAILS."""
        checker = _checker()
        checker.MAX_DETAILS = 3
        engine = checker.engine

        mock_conn = MagicMock()
        # DB says value 1 conflicts
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([(1,)])
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Many rows with id=1
        df = pd.DataFrame({"id": [1] * 10})
        count, details = checker._check_single_column(df, "public", "t", "id")
        assert count == 1
        assert len(details) == 3  # capped at MAX_DETAILS


# ===========================================================================
# _check_multi_column (lines 223-281)
# ===========================================================================

class TestCheckMultiColumn:

    def test_no_conflicts(self):
        checker = _checker()
        engine = checker.engine

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        assert count == 0
        assert details == []

    def test_with_conflicts(self):
        checker = _checker()
        engine = checker.engine

        call_count = [0]

        def side_effect_connect():
            ctx = MagicMock()
            mock_conn = MagicMock()
            mock_result = MagicMock()
            call_count[0] += 1
            # First combo: conflict found (count=1)
            # Second combo: no conflict (count=0)
            if call_count[0] == 1:
                mock_result.scalar.return_value = 1
            else:
                mock_result.scalar.return_value = 0
            mock_conn.execute.return_value = mock_result
            ctx.__enter__ = MagicMock(return_value=mock_conn)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        engine.connect = side_effect_connect

        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        assert count == 1
        assert len(details) >= 1
        assert details[0].match_values == {"a": 1, "b": "x"}
        assert details[0].conflict_type == "pk_duplicate"

    def test_nan_in_multi_column_uses_is_null(self):
        """When a match column value is NaN, WHERE uses IS NULL."""
        checker = _checker()
        engine = checker.engine

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"a": [1], "b": [None]})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        assert count == 0
        # Verify the query included IS NULL
        executed_query = mock_conn.execute.call_args[0][0]
        assert "IS NULL" in str(executed_query)

    def test_exception_per_combo_continues(self):
        """If a single combo query fails, processing continues (line 272-274)."""
        checker = _checker()
        engine = checker.engine

        call_count = [0]

        def side_effect_connect():
            ctx = MagicMock()
            mock_conn = MagicMock()
            call_count[0] += 1
            if call_count[0] == 1:
                mock_conn.execute.side_effect = RuntimeError("query fail")
            else:
                mock_result = MagicMock()
                mock_result.scalar.return_value = 1
                mock_conn.execute.return_value = mock_result
            ctx.__enter__ = MagicMock(return_value=mock_conn)
            ctx.__exit__ = MagicMock(return_value=False)
            return ctx

        engine.connect = side_effect_connect

        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        # First combo failed, second found a conflict
        assert count == 1

    def test_over_1000_conflicts_breaks(self):
        """Safety break at >1000 conflicts (lines 277-279)."""
        checker = _checker()
        checker.MAX_DETAILS = 5  # low to avoid huge detail list
        engine = checker.engine

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1  # every combo is a conflict
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        # Create 1500 unique combos
        df = pd.DataFrame({"a": range(1500), "b": range(1500)})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        # Should break after crossing 1000
        assert count == 1001
        assert len(details) <= checker.MAX_DETAILS

    def test_details_capped_at_max(self):
        """Details list is sliced to MAX_DETAILS at the end (line 281)."""
        checker = _checker()
        checker.MAX_DETAILS = 2
        engine = checker.engine

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"a": [1, 2, 3], "b": [4, 5, 6]})
        count, details = checker._check_multi_column(df, "public", "t", ["a", "b"])
        assert count == 3
        assert len(details) <= 2


# ===========================================================================
# _qualified_name
# ===========================================================================

class TestQualifiedName:

    def test_postgres_with_schema(self):
        c = _checker("postgres")
        assert c._qualified_name("public", "test") == '"public"."test"'

    def test_postgres_no_schema(self):
        c = _checker("postgres")
        assert c._qualified_name("", "test") == '"test"'

    def test_mysql_with_schema(self):
        c = _checker("mysql")
        assert c._qualified_name("mydb", "test") == "`mydb`.`test`"

    def test_mysql_no_schema(self):
        c = _checker("mysql")
        assert c._qualified_name("", "test") == "`test`"

    def test_mariadb_with_schema(self):
        c = _checker("mariadb")
        assert c._qualified_name("mydb", "test") == "`mydb`.`test`"

    def test_oracle_with_schema(self):
        c = _checker("oracle")
        result = c._qualified_name("myschema", "mytable")
        assert result == '"MYSCHEMA"."MYTABLE"'

    def test_oracle_no_schema(self):
        c = _checker("oracle")
        result = c._qualified_name("", "mytable")
        assert result == '"MYTABLE"'

    def test_mssql_with_schema(self):
        c = _checker("mssql")
        assert c._qualified_name("dbo", "test") == '"dbo"."test"'

    def test_mssql_no_schema(self):
        c = _checker("mssql")
        assert c._qualified_name("", "test") == '"test"'
