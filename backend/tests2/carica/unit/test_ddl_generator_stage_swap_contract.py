"""Stage+swap DDL contracts (C5 fix 3 — UNDO safety cross-dialect).

Motivazione
-----------
Per TRUNCATE_INSERT + ALL_OR_NOTHING su dataset molto grandi (sopra
CARICA_STAGE_SWAP_THRESHOLD righe), DELETE+INSERT in una singola txn
satura UNDO/log del DBMS (ORA-01555, undo log too large MySQL,
transaction log full MSSQL, bloat Postgres). Il pattern stage+swap
risolve:

1. CREATE tabella shadow `{table}__stg` con struttura = target
2. INSERT tutti i batch nella shadow (zero pressione su target)
3. RENAME atomico: target→backup, shadow→target, DROP backup

Questo contract verifica che il DDL generator emette il SQL corretto
per ogni dialetto supportato.
"""

import pytest

from app.core.carica.ddl_generator import DDLGenerator


pytestmark = [pytest.mark.unit, pytest.mark.carica, pytest.mark.contract]


# generate_create_like — tabella shadow con stessa struttura


def test_postgres_create_like_uses_including_all():
    """Postgres: CREATE TABLE stg (LIKE src INCLUDING ALL) copia
    defaults, indexes, constraints, storage. Preserva tutto."""
    gen = DDLGenerator("postgres")

    sql = gen.generate_create_like("public", "orders", "orders__stg")

    up = sql.upper()
    assert "CREATE TABLE" in up
    assert "LIKE" in up
    assert "INCLUDING ALL" in up, (
        f"Postgres DEVE usare INCLUDING ALL per preservare indexes + "
        f"constraints. Emesso: {sql}"
    )
    assert "ORDERS__STG" in up and "ORDERS" in up


def test_mysql_create_like_uses_native_like():
    """MySQL: CREATE TABLE stg LIKE src copia colonne + indexes
    (NON FK). Per il nostro use case (TRUNCATE_INSERT) va bene."""
    gen = DDLGenerator("mysql")

    sql = gen.generate_create_like("mydb", "items", "items__stg")

    up = sql.upper()
    assert "CREATE TABLE" in up
    assert "LIKE" in up
    assert "ITEMS__STG" in up


def test_oracle_create_like_uses_as_select_empty():
    """Oracle: niente CREATE LIKE nativo. Usiamo CREATE TABLE AS
    SELECT WHERE 1=0 — copia colonne + NOT NULL derivati, NON
    preserva PK/UK/indexes/FK. Limitazione documentata.

    La scelta del WHERE 1=0 e' intenzionale: deve essere una
    condizione sempre falsa per non copiare righe."""
    gen = DDLGenerator("oracle")

    sql = gen.generate_create_like("HR", "EMPLOYEES", "EMPLOYEES__STG")

    up = sql.upper()
    assert "CREATE TABLE" in up
    assert "AS SELECT" in up
    assert "1=0" in up.replace(" ", "") or "1 = 0" in up, (
        f"Oracle CREATE LIKE deve usare 'WHERE 1=0' per creare shell "
        f"vuota. Emesso: {sql}"
    )
    assert "EMPLOYEES__STG" in up


def test_mssql_create_like_uses_select_into_empty():
    """MSSQL: SELECT * INTO stg FROM src WHERE 1=0.
    Come Oracle: copia colonne, non preserva indexes/constraints."""
    gen = DDLGenerator("mssql")

    sql = gen.generate_create_like("dbo", "logs", "logs__stg")

    up = sql.upper()
    # Accettiamo entrambe le forme: SELECT INTO oppure CREATE TABLE AS
    assert "SELECT" in up and "INTO" in up
    assert "1=0" in up.replace(" ", "") or "1 = 0" in up
    assert "LOGS__STG" in up


# generate_swap_rename — 3-way rename atomico per dialetto


def test_postgres_swap_uses_two_alter_renames():
    """Postgres: ALTER TABLE RENAME (2x). Il caller li wrappa in
    un'unica transazione con BEGIN/COMMIT."""
    gen = DDLGenerator("postgres")

    stmts = gen.generate_swap_rename("public", "orders", "orders__stg")

    assert isinstance(stmts, list)
    assert len(stmts) == 2, (
        f"Postgres deve emettere 2 statement RENAME (src→backup, "
        f"stg→src). Emessi: {stmts}"
    )
    joined = " ".join(stmts).upper()
    assert "ALTER TABLE" in joined
    assert "RENAME TO" in joined
    # Primo rename deve coinvolgere "orders", secondo rename deve
    # portare "orders__stg" al nome "orders"
    assert "ORDERS__STG" in joined
    assert "ORDERS" in joined


def test_mysql_swap_uses_single_atomic_rename():
    """MySQL: RENAME TABLE a TO x, b TO a, x TO b — singola
    statement, atomica (no window di inconsistenza)."""
    gen = DDLGenerator("mysql")

    stmts = gen.generate_swap_rename("mydb", "items", "items__stg")

    assert isinstance(stmts, list)
    assert len(stmts) == 1, (
        f"MySQL deve usare UNA sola statement RENAME TABLE con 3 "
        f"coppie per atomicita'. Emesse: {stmts}"
    )
    up = stmts[0].upper()
    assert "RENAME TABLE" in up
    # Deve contenere 3 rename (src→backup, stg→src, backup→dropping)
    # Tipico: "RENAME TABLE a TO backup_x, stg TO a, backup_x TO stale"
    # Contiamo almeno 2 occorrenze di " TO "
    assert up.count(" TO ") >= 2, (
        f"MySQL RENAME TABLE deve avere almeno 2 rename per swap 3-way. "
        f"Emesso: {stmts[0]}"
    )


def test_oracle_swap_uses_two_rename_ddl():
    """Oracle: RENAME (statement non-qualified). Ogni RENAME e' DDL
    auto-commit: la finestra di inconsistenza tra i due rename e'
    sub-secondo, documentata."""
    gen = DDLGenerator("oracle")

    stmts = gen.generate_swap_rename("HR", "EMPLOYEES", "EMPLOYEES__STG")

    assert isinstance(stmts, list)
    assert len(stmts) == 2
    joined = " ".join(stmts).upper()
    # Oracle RENAME syntax: "RENAME old TO new"
    assert "RENAME" in joined
    assert "EMPLOYEES" in joined


def test_mssql_swap_uses_sp_rename():
    """MSSQL: sp_rename (2x) dentro un BEGIN TRAN … COMMIT.
    Il caller wrappa."""
    gen = DDLGenerator("mssql")

    stmts = gen.generate_swap_rename("dbo", "logs", "logs__stg")

    assert isinstance(stmts, list)
    assert len(stmts) == 2
    joined = " ".join(stmts).upper()
    assert "SP_RENAME" in joined
    assert "LOGS__STG" in joined or "LOGS" in joined


# generate_drop_if_exists — cleanup shadow orfana


def test_drop_if_exists_oracle_uses_plsql_block():
    """Oracle non ha DROP TABLE IF EXISTS: serve PL/SQL con
    EXCEPTION WHEN OTHERS per non fallire se la tabella non c'e'.
    (Gia' coperto dal metodo esistente, test sentinella.)"""
    gen = DDLGenerator("oracle")

    sql = gen.generate_drop_if_exists("HR", "EMPLOYEES__STG")

    up = sql.upper()
    # Oracle: "BEGIN EXECUTE IMMEDIATE 'DROP TABLE ...'; EXCEPTION ..."
    assert "DROP TABLE" in up
    # Deve essere tollerante alla mancanza della tabella
    assert "EXCEPTION" in up or "BEGIN" in up


def test_drop_if_exists_postgres_native():
    gen = DDLGenerator("postgres")

    sql = gen.generate_drop_if_exists("public", "orders__stg")

    assert "DROP TABLE IF EXISTS" in sql.upper()
    assert "ORDERS__STG" in sql.upper()
