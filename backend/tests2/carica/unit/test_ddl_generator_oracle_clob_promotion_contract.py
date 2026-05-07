"""Auto-promote VARCHAR2(4000)→CLOB su Oracle per colonne lunghe (C5 fix 2).

Motivazione
-----------
Il mapping default Oracle per str/string/category e' `VARCHAR2(4000)`.
Una colonna con testo > 4000 char fa fallire l'INSERT con
`ORA-12899: value too large for column`. Fix: `generate_create_table`
accetta `max_lengths: Dict[col_name, int]` (calcolato dal
DataFrame); se su Oracle la colonna e' VARCHAR2 e la sua max_length
supera 4000, il tipo viene promosso a CLOB.

Altri dialetti (Postgres TEXT, MSSQL NVARCHAR(MAX), MySQL TEXT) hanno
gia' tipi senza limite → non serve promozione.
"""

from unittest.mock import MagicMock

import pytest

from app.core.carica.ddl_generator import DDLGenerator
from app.core.carica.models import ColumnMapping


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def _col(source, dest, dest_type="str"):
    return ColumnMapping(source_column=source, dest_column=dest, dest_type=dest_type)


# Oracle: promozione attiva


def test_oracle_long_string_column_promoted_to_clob():
    """Oracle: se max_length > 4000, VARCHAR2(4000) → CLOB."""
    gen = DDLGenerator("oracle")
    cols = [_col("description", "DESCRIPTION", "str")]

    sql = gen.generate_create_table(
        "HR", "PRODUCTS", cols, max_lengths={"description": 5000},
    )

    assert "CLOB" in sql.upper()
    assert "VARCHAR2(4000)" not in sql.upper()


def test_oracle_short_string_column_stays_varchar2():
    """Oracle: se max_length <= 4000, resta VARCHAR2(4000)."""
    gen = DDLGenerator("oracle")
    cols = [_col("title", "TITLE", "str")]

    sql = gen.generate_create_table(
        "HR", "PRODUCTS", cols, max_lengths={"title": 100},
    )

    assert "VARCHAR2(4000)" in sql.upper()
    # CLOB NON deve comparire per questa colonna — ma la stringa CLOB
    # potrebbe apparire in altre parti (altre colonne). Verifichiamo
    # che il tipo del nostro field specifico sia VARCHAR2.
    assert "CLOB" not in sql.upper()


def test_oracle_boundary_exactly_4000_stays_varchar2():
    """Oracle: esattamente 4000 char sta in VARCHAR2(4000). Solo > 4000 promuove."""
    gen = DDLGenerator("oracle")
    cols = [_col("code", "CODE", "str")]

    sql = gen.generate_create_table(
        "HR", "PRODUCTS", cols, max_lengths={"code": 4000},
    )

    assert "VARCHAR2(4000)" in sql.upper()
    assert "CLOB" not in sql.upper()


def test_oracle_promotion_only_affects_long_columns():
    """Promozione mirata: solo le colonne sopra soglia, non tutte."""
    gen = DDLGenerator("oracle")
    cols = [
        _col("title", "TITLE", "str"),       # corta
        _col("body", "BODY", "str"),         # lunga
        _col("tag", "TAG", "str"),           # corta
    ]

    sql = gen.generate_create_table(
        "HR", "DOCS", cols,
        max_lengths={"title": 80, "body": 10000, "tag": 20},
    )

    # BODY e' promosso a CLOB
    assert "BODY" in sql.upper() and "CLOB" in sql.upper()
    # TITLE e TAG restano VARCHAR2
    assert "VARCHAR2(4000)" in sql.upper()


# Oracle: missing max_lengths (legacy behavior)


def test_oracle_without_max_lengths_kwarg_keeps_legacy_varchar2():
    """Se max_lengths non viene passato, behavior legacy (VARCHAR2(4000))
    per retrocompat con call site che non lo calcolano."""
    gen = DDLGenerator("oracle")
    cols = [_col("description", "DESCRIPTION", "str")]

    sql = gen.generate_create_table("HR", "PRODUCTS", cols)

    assert "VARCHAR2(4000)" in sql.upper()


# Altri dialetti: nessuna promozione (il tipo base non ha limite)


def test_postgres_long_string_is_already_unlimited():
    """Postgres usa TEXT, gia' senza limite. max_lengths ignorato."""
    gen = DDLGenerator("postgres")
    cols = [_col("description", "description", "str")]

    sql = gen.generate_create_table(
        "public", "products", cols, max_lengths={"description": 10000},
    )

    # TEXT gia' copre stringhe di qualsiasi lunghezza — no promozione necessaria
    assert "TEXT" in sql.upper() or "VARCHAR" in sql.upper()


def test_mssql_long_string_is_already_unlimited():
    """MSSQL NVARCHAR(MAX) gia' senza limite. max_lengths ignorato."""
    gen = DDLGenerator("mssql")
    cols = [_col("description", "description", "str")]

    sql = gen.generate_create_table(
        "dbo", "products", cols, max_lengths={"description": 10000},
    )

    # NVARCHAR(MAX) gia' copre stringhe di qualsiasi lunghezza
    assert "NVARCHAR" in sql.upper() or "TEXT" in sql.upper()
