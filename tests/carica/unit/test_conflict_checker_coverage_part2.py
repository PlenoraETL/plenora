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
# _quote_col
# ===========================================================================

class TestQuoteCol:

    def test_postgres(self):
        c = _checker("postgres")
        assert c._quote_col("name") == '"name"'

    def test_mysql(self):
        c = _checker("mysql")
        assert c._quote_col("name") == "`name`"

    def test_mariadb(self):
        c = _checker("mariadb")
        assert c._quote_col("name") == "`name`"

    def test_oracle_uppercases(self):
        c = _checker("oracle")
        assert c._quote_col("name") == '"NAME"'

    def test_mssql(self):
        c = _checker("mssql")
        assert c._quote_col("name") == '"name"'


# ===========================================================================
# Integration-style: check() with real internal dup + mocked DB
# ===========================================================================

class TestCheckIntegration:

    def test_internal_dups_and_db_conflicts(self):
        checker = _checker()
        engine = checker.engine

        mock_conn = MagicMock()
        # DB says value 1 already exists
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([(1,)])
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1, 1, 2, 3]})  # 1 internal dup on "id"
        report = checker.check(df, "public", "t", match_columns=["id"])
        assert report.has_conflicts is True
        assert report.internal_duplicates == 1
        assert report.db_conflicts == 1  # value 1 conflicts
        assert report.total_conflicts == 2

    def test_no_internal_no_db(self):
        checker = _checker()
        engine = checker.engine

        mock_conn = MagicMock()
        mock_result = MagicMock()
        mock_result.__iter__ = lambda self: iter([])
        mock_conn.execute.return_value = mock_result
        engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        engine.connect.return_value.__exit__ = MagicMock(return_value=False)

        df = pd.DataFrame({"id": [1, 2, 3]})
        report = checker.check(df, "public", "t", match_columns=["id"])
        assert report.has_conflicts is False
        assert report.total_conflicts == 0
