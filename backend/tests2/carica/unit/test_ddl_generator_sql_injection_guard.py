"""Regression tests for user-controlled CARICA DDL fragments."""

from __future__ import annotations

import pytest

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.security]


@pytest.mark.parametrize("db_type", ["postgres", "mssql", "mysql", "oracle"])
@pytest.mark.parametrize("payload", [
    "TEXT); DROP TABLE audit_log; --",
    "TEXT NOT NULL",
    "VARCHAR(10) DEFAULT 'admin'",
])
def test_native_type_rejects_sql_and_constraints(db_type, payload):
    generator = DDLGenerator(db_type)
    column = ColumnMapping("source", "target", dest_type=payload)

    with pytest.raises(ValueError, match="Tipo SQL"):
        generator.generate_create_table("public", "safe", [column], {"source": "str"})


@pytest.mark.parametrize("native_type", [
    "VARCHAR(255)",
    "NUMERIC(12,2)",
    "DOUBLE PRECISION",
    "TIMESTAMP(6) WITH TIME ZONE",
    "NVARCHAR(MAX)",
])
def test_legitimate_native_types_remain_supported(native_type):
    generator = DDLGenerator("postgres")
    column = ColumnMapping("source", "target", dest_type=native_type)

    sql = generator.generate_create_table("public", "safe", [column], {"source": "str"})

    assert native_type in sql


@pytest.mark.parametrize(
    ("db_type", "schema", "table", "expected"),
    [
        ("postgres", 'pub"lic', 'ta"ble', '"pub""lic"."ta""ble"'),
        ("mysql", "pub`lic", "ta`ble", "`pub``lic`.`ta``ble`"),
    ],
)
def test_identifiers_escape_dialect_delimiters(db_type, schema, table, expected):
    assert DDLGenerator(db_type).qualified_table_name(schema, table) == expected
