"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-013-CONFLICT-CHECKER-DDL-DEPENDENCY-INJECTION.

Locks the dependency-injection boundary post-TE11-R4 closure:
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL set before any pg_connection import.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.core.carica.conflict_checker import ConflictChecker
from app.core.carica.ddl_generator import DDLGenerator
from tests2.shared._ast_identifiers import imports_symbol_from


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_CHECKER_PY = _BACKEND_ROOT / "app" / "core" / "carica" / "conflict_checker.py"
_PREFLIGHT_PY = (
    _BACKEND_ROOT / "app" / "core" / "carica" / "writer_preflight_lifecycle.py"
)


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# 1. Constructor: injected DDLGenerator is stored as self.ddl


def test_constructor_accepts_injected_ddl():
    engine = MagicMock()
    ddl = DDLGenerator("postgres")
    checker = ConflictChecker(engine, "postgres", ddl=ddl)
    assert checker.ddl is ddl


def test_constructor_builds_default_ddl_when_not_injected():
    """Backward-compat: callers that don't pass `ddl=` still work."""
    engine = MagicMock()
    checker = ConflictChecker(engine, "postgres")
    assert isinstance(checker.ddl, DDLGenerator)
    assert checker.ddl.db_type == "postgres"


def test_default_ddl_uses_lowercased_db_type():
    """Mirrors `self.db_type = db_type.lower()`. The auto-built
    DDLGenerator must use the normalized lowercase form."""
    engine = MagicMock()
    checker = ConflictChecker(engine, "Postgres")  # mixed case
    assert checker.db_type == "postgres"
    assert checker.ddl.db_type == "postgres"


def test_constructor_ddl_is_keyword_only():
    """The ddl injection MUST be keyword-only so positional callsites
    `ConflictChecker(engine, db_type)` (router.py + legacy tests)
    keep working without accidental rebinding."""
    text = _read(_CHECKER_PY)
    # Match the `__init__` signature.
    m = re.search(r"def __init__\(\s*self,\s*([^)]*)\)\s*:", text, re.DOTALL)
    assert m is not None
    sig = m.group(1)
    assert "*," in sig, "ddl must be kw-only (signature must contain `*,`)"
    assert "ddl:" in sig


# 2. _qualified_name and _quote_col delegate to DDLGenerator


@pytest.mark.parametrize(
    "db_type,schema,table,expected",
    [
        ("postgres", "public", "tbl", '"public"."tbl"'),
        ("mssql",    "dbo",    "tbl", '"dbo"."tbl"'),
        ("mysql",    "mydb",   "tbl", "`mydb`.`tbl`"),
        ("mariadb",  "mydb",   "tbl", "`mydb`.`tbl`"),
        ("oracle",   "scott",  "tbl", '"SCOTT"."TBL"'),
        # No-schema variants.
        ("postgres", "",       "tbl", '"tbl"'),
        ("mysql",    "",       "tbl", "`tbl`"),
        ("oracle",   "",       "tbl", '"TBL"'),
    ],
)
def test_qualified_name_dialect_correct(db_type, schema, table, expected):
    engine = MagicMock()
    checker = ConflictChecker(engine, db_type)
    assert checker._qualified_name(schema, table) == expected


@pytest.mark.parametrize(
    "db_type,col,expected",
    [
        ("postgres", "mycol",  '"mycol"'),
        ("mssql",    "mycol",  '"mycol"'),
        ("mysql",    "mycol",  "`mycol`"),
        ("mariadb",  "mycol",  "`mycol`"),
        ("oracle",   "mycol",  '"MYCOL"'),
        ("oracle",   "MyCol",  '"MYCOL"'),
    ],
)
def test_quote_col_dialect_correct(db_type, col, expected):
    engine = MagicMock()
    checker = ConflictChecker(engine, db_type)
    assert checker._quote_col(col) == expected


def test_qualified_name_uses_injected_ddl_not_self_db_type():
    """Sanity: when `ddl=` is injected, the helper goes through the
    DDLGenerator instance — even if `self.db_type` would say something
    different. Locks the delegation: no residual `if self.db_type ==`
    branching."""
    engine = MagicMock()
    pg_ddl = DDLGenerator("postgres")
    # Inject postgres DDL but pass mysql db_type as red herring.
    checker = ConflictChecker(engine, "mysql", ddl=pg_ddl)
    # Postgres quoting is "<schema>"."<table>", not backticks.
    assert checker._qualified_name("public", "tbl") == '"public"."tbl"'
    assert checker._quote_col("mycol") == '"mycol"'


def test_quote_col_uses_injected_ddl_not_self_db_type():
    engine = MagicMock()
    oracle_ddl = DDLGenerator("oracle")
    checker = ConflictChecker(engine, "postgres", ddl=oracle_ddl)
    # Oracle uppercase + double-quote, not postgres lowercase.
    assert checker._quote_col("MyCol") == '"MYCOL"'


# 3. Source-bound: no inline duplicated dialect branching


def test_helpers_delegate_to_ddl_no_inline_branching():
    """Locks TE11-R4 closure: the bodies of `_qualified_name` and
    `_quote_col` must NOT contain inline `if self.db_type ==`
    branching anymore — only delegation to `self.ddl.*`."""
    text = _read(_CHECKER_PY)

    # Extract `_qualified_name` body.
    m_qn = re.search(
        r"def _qualified_name\(self,[^)]*\)[^:]*:\s*((?:.+\n)+?)\n    def ",
        text,
    )
    assert m_qn is not None, "could not locate _qualified_name body"
    qn_body = m_qn.group(1)
    assert "self.ddl.qualified_table_name" in qn_body, (
        "_qualified_name must delegate to self.ddl.qualified_table_name"
    )
    assert "if self.db_type" not in qn_body, (
        "_qualified_name must NOT branch on self.db_type inline; "
        "delegation only"
    )
    assert "`" not in qn_body, (
        "_qualified_name must NOT contain inline backtick literals"
    )

    # Extract `_quote_col` body. End-of-file: anchor at end.
    m_qc = re.search(
        r"def _quote_col\(self,[^)]*\)[^:]*:\s*((?:.+\n)+?)(?:\nclass |\n\ndef _serialize|\Z)",
        text,
    )
    assert m_qc is not None, "could not locate _quote_col body"
    qc_body = m_qc.group(1)
    assert "self.ddl.quote_column" in qc_body, (
        "_quote_col must delegate to self.ddl.quote_column"
    )
    assert "if self.db_type" not in qc_body, (
        "_quote_col must NOT branch on self.db_type inline; "
        "delegation only"
    )


def test_module_imports_ddl_generator():
    text = _read(_CHECKER_PY)
    assert imports_symbol_from(text, "app.core.carica.ddl_generator", "DDLGenerator")


# 4. Caller WriterPreflightLifecycleMixin.check_conflicts injects self.ddl


def test_preflight_lifecycle_injects_self_ddl_into_checker():
    """The writer's `check_conflicts` must pass `self.ddl` so the
    ConflictChecker reuses the existing DDLGenerator instance instead
    of building a duplicate."""
    text = _read(_PREFLIGHT_PY)
    # Match the `ConflictChecker(...)` call inside `check_conflicts`.
    m = re.search(
        r"checker\s*=\s*ConflictChecker\(([^)]+)\)",
        text,
    )
    assert m is not None, "ConflictChecker(...) not found in writer_preflight_lifecycle.py"
    args = m.group(1)
    assert "ddl=self.ddl" in args, (
        "WriterPreflightLifecycleMixin.check_conflicts must inject "
        "`ddl=self.ddl` into ConflictChecker(...)"
    )


# 5. Generated SQL preserves dialect quoting end-to-end (smoke)


def test_check_uses_quoted_names_in_select_for_postgres(monkeypatch):
    """Smoke-end-to-end: the SELECT issued by `_check_single_column`
    uses the quoted column + qualified table name from DDLGenerator.

    We mock the engine.connect() call to capture the issued SQL and
    return zero conflicting values."""
    import pandas as pd

    captured_sql = []

    class _FakeResult:
        def __iter__(self):
            return iter(())

    class _FakeConn:
        def execute(self, statement, params=None):
            # text(...) wraps; capture .text attribute when present.
            captured_sql.append(getattr(statement, "text", str(statement)))
            return _FakeResult()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = _FakeConn()

    checker = ConflictChecker(fake_engine, "postgres")
    df = pd.DataFrame({"id": [1, 2, 3]})
    count, details = checker._check_single_column(df, "public", "tbl", "id")

    assert count == 0
    assert details == []
    # SELECT must include "public"."tbl" + "id" quoting.
    assert any('"public"."tbl"' in s and '"id"' in s for s in captured_sql)


def test_check_uses_backticks_for_mysql(monkeypatch):
    import pandas as pd

    captured_sql = []

    class _FakeResult:
        def __iter__(self):
            return iter(())

    class _FakeConn:
        def execute(self, statement, params=None):
            captured_sql.append(getattr(statement, "text", str(statement)))
            return _FakeResult()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    fake_engine = MagicMock()
    fake_engine.connect.return_value = _FakeConn()

    checker = ConflictChecker(fake_engine, "mysql")
    df = pd.DataFrame({"sku": ["a", "b"]})
    checker._check_single_column(df, "shop", "items", "sku")

    assert any("`shop`.`items`" in s and "`sku`" in s for s in captured_sql)
