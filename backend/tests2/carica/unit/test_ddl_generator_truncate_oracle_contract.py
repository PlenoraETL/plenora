"""TRUNCATE su Oracle → DELETE FROM (C5 fix 1).

Motivazione
-----------
Oracle tratta TRUNCATE come DDL: auto-commit IMPLICITO al suo
completamento. Se il flow e' `TRUNCATE → INSERT` e l'INSERT fallisce,
il rollback NON puo' ripristinare le righe cancellate dal TRUNCATE
(commit gia' avvenuto). Risultato: tabella vuota, perdita dati
deterministica per write_mode=truncate_insert.

Fix: su Oracle E MySQL usiamo `DELETE FROM` (DML, transazionale).
Il rollback copre anche la cancellazione. Performance inferiore
per tabelle grandi, ma safe. Postgres e SQL Server restano su
TRUNCATE (transazionale nella stessa connection).

Nota MySQL: anche TRUNCATE TABLE su MySQL e' DDL con auto-commit
implicito (stessa semantica di Oracle, non transazionale), quindi
il bug pre-fix affliggeva MySQL nello stesso identico modo.
"""

import pytest

from app.core.carica.ddl_generator import DDLGenerator


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


def test_oracle_truncate_uses_delete_from_for_transactional_safety():
    """Su Oracle, generate_truncate deve emettere DELETE FROM (DML),
    non TRUNCATE TABLE (DDL auto-commit)."""
    gen = DDLGenerator("oracle")

    sql = gen.generate_truncate("HR", "EMPLOYEES")

    # Deve contenere DELETE FROM, NON TRUNCATE
    assert "DELETE FROM" in sql.upper(), (
        f"Oracle generate_truncate deve usare DELETE FROM (transazionale). "
        f"Emesso: {sql}"
    )
    assert "TRUNCATE TABLE" not in sql.upper(), (
        f"Oracle NON deve usare TRUNCATE TABLE (DDL auto-commit causa "
        f"perdita dati su INSERT failure). Emesso: {sql}"
    )
    # Deve includere il nome qualificato
    assert "HR" in sql.upper() and "EMPLOYEES" in sql.upper()


def test_postgres_truncate_still_uses_truncate_table():
    """Postgres TRUNCATE e' transazionale: resta TRUNCATE TABLE."""
    gen = DDLGenerator("postgres")

    sql = gen.generate_truncate("public", "orders")

    assert "TRUNCATE TABLE" in sql.upper()
    assert "DELETE FROM" not in sql.upper()


def test_mysql_truncate_uses_delete_from_for_transactional_safety():
    """Su MySQL, TRUNCATE TABLE e' DDL con implicit commit (stesso
    bug di Oracle). generate_truncate deve emettere DELETE FROM per
    mantenere la transazione aperta e permettere rollback atomico
    di truncate+insert in modalita' ALL_OR_NOTHING."""
    gen = DDLGenerator("mysql")

    sql = gen.generate_truncate("mydb", "items")

    assert "DELETE FROM" in sql.upper(), (
        f"MySQL generate_truncate deve usare DELETE FROM "
        f"(TRUNCATE TABLE ha implicit commit anche su MySQL). "
        f"Emesso: {sql}"
    )
    assert "TRUNCATE TABLE" not in sql.upper()
    assert "ITEMS" in sql.upper()


def test_mssql_truncate_still_uses_truncate_table():
    gen = DDLGenerator("mssql")

    sql = gen.generate_truncate("dbo", "logs")

    assert "TRUNCATE TABLE" in sql.upper()
