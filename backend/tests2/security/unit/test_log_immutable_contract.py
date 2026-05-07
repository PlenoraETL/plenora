"""
Contract test trigger append-only su plenora.logs + plenora.audit_actions.

Scope:
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.requires_postgres,
    pytest.mark.developer_nightly,
    pytest.mark.db_live_opt_in,
]


# MIGRATION PATH

MIGRATION_FILENAME = "2026-04-21_log_immutable_triggers.sql"


def _migration_path() -> Path:
    # __file__ = backend/tests2/security/unit/test_log_immutable_contract.py
    # parents[3] = backend
    return Path(__file__).resolve().parents[3] / "migrations" / MIGRATION_FILENAME


@pytest.fixture(scope="module")
def migration_sql() -> str:
    path = _migration_path()
    assert path.exists(), f"Migration attesa a {path}"
    return path.read_text(encoding="utf-8")


# A) STATIC CONTRACT (no PG)


def test_migration_defines_block_function(migration_sql: str) -> None:
    """Deve esistere la funzione trigger `plenora.block_log_modifications`."""
    assert "CREATE OR REPLACE FUNCTION plenora.block_log_modifications()" in migration_sql


def test_function_rejects_update_always(migration_sql: str) -> None:
    """Il ramo TG_OP='UPDATE' deve sempre `RAISE EXCEPTION`, nessun bypass."""
    # Normalizza whitespace per asserzione robusta su newline-spacing.
    content = " ".join(migration_sql.split())
    assert "IF TG_OP = 'UPDATE' THEN RAISE EXCEPTION" in content, (
        "REGRESSIONE: il ramo UPDATE deve sollevare EXCEPTION senza "
        "condizioni. La migration ha perso l'immutability assoluta."
    )


def test_function_allows_delete_only_with_bypass(migration_sql: str) -> None:
    """Il bypass `plenora.retention_cleanup` deve essere l'UNICA via
    d'uscita per il ramo DELETE. Il pattern atteso:
        retention_flag := current_setting('plenora.retention_cleanup', true);
        IF retention_flag = 'true' THEN RETURN OLD;
    """
    assert "current_setting('plenora.retention_cleanup'" in migration_sql, (
        "REGRESSIONE: rimosso il check del bypass GUC, il DELETE verrebbe "
        "sempre bloccato anche per i retention job autorizzati."
    )
    assert "retention_flag = 'true'" in migration_sql, (
        "REGRESSIONE: rimosso il confronto con 'true', il bypass non "
        "si attiva piu'."
    )


def test_migration_covers_logs_table(migration_sql: str) -> None:
    """Entrambi i trigger (UPDATE + DELETE) su `plenora.logs`."""
    assert "CREATE TRIGGER trg_logs_block_update" in migration_sql
    assert "CREATE TRIGGER trg_logs_block_delete" in migration_sql
    # Collegati alla tabella giusta e in BEFORE + FOR EACH ROW.
    content = " ".join(migration_sql.split())
    assert "BEFORE UPDATE ON plenora.logs FOR EACH ROW" in content
    assert "BEFORE DELETE ON plenora.logs FOR EACH ROW" in content


def test_migration_covers_audit_actions_table(migration_sql: str) -> None:
    """Entrambi i trigger (UPDATE + DELETE) su `plenora.audit_actions`."""
    assert "CREATE TRIGGER trg_audit_actions_block_update" in migration_sql
    assert "CREATE TRIGGER trg_audit_actions_block_delete" in migration_sql
    content = " ".join(migration_sql.split())
    assert "BEFORE UPDATE ON plenora.audit_actions FOR EACH ROW" in content
    assert "BEFORE DELETE ON plenora.audit_actions FOR EACH ROW" in content


def test_migration_is_idempotent(migration_sql: str) -> None:
    """Re-run safe: `CREATE OR REPLACE FUNCTION` + `DROP TRIGGER IF EXISTS`
    per tutti i trigger. Senza questo la migration non e' replayable.
    """
    assert migration_sql.count("DROP TRIGGER IF EXISTS") >= 4, (
        "Ogni trigger deve essere preceduto da DROP IF EXISTS per "
        "mantenere idempotenza della migration"
    )
    assert "CREATE OR REPLACE FUNCTION" in migration_sql


def test_migration_covers_only_declared_tables(migration_sql: str) -> None:
    """Perimetro stretto: SOLO plenora.logs e plenora.audit_actions.
    Se in futuro qualcuno aggiunge trigger su altre tabelle qui, il test
    forza review esplicita (scope creep bloccato al commit).
    """
    import re
    # \b davanti a ON per evitare falsi match su "FUNCTION plenora.xxx"
    # (la stringa "TION plenora." termina con un substring "ON plenora.").
    tables = re.findall(r"\bON\s+plenora\.(\w+)", migration_sql)
    unique_tables = set(tables)
    assert unique_tables == {"logs", "audit_actions"}, (
        f"Perimetro migration cambiato: tabelle trovate = {unique_tables}. "
        f"Atteso: {{'logs', 'audit_actions'}}. Se lo scope si allarga, "
        f"aggiorna il test e documenta in invariants.md."
    )


# B) INTEGRATION CONTRACT (richiede PG live, opt-in esplicito)
#
# Questi test fanno `psycopg2.connect` reale e applicano la migration
# `2026-04-21_log_immutable_triggers.sql` su un DB di test condiviso.
# Sono marcati `integration` E gated da una env opt-in esplicita.
#
# Run policy:
#   - default `pytest tests2/security`            -> SKIP (env opt-in mancante)
#   - default `pytest tests2/`                    -> SKIP (env opt-in mancante)
#   - opt-in  `URBY_RUN_DB_LIVE_TESTS=1 + DATABASE_URL` -> RUN
#   - selezione `pytest -m integration`           -> raccolti, ma comunque
#     skip se env opt-in non e' settata (env-gate ha priorita').
#
# Razionale del double-gate (env + marker):
#   - su Windows, `psycopg2.connect` puo' produrre access violation nativa
#     prima di sollevare OperationalError. Il probe `connect_timeout=2` non
#     e' garanzia: se il driver crasha lato C, il try/except Python non
#     intercetta. La sola difesa solida e' NON chiamare `psycopg2.connect`.
#   - skipif su `DATABASE_URL` non basta: in dev locale `.env` carica
#     DATABASE_URL anche quando i dev non vogliono che i DB-live partano.
#   - env opt-in `URBY_RUN_DB_LIVE_TESTS=1` rende l'attivazione un atto
#     volontario, scriptabile in CI quando esiste un job dedicato DB-live.
#
# Test che richiedono `pg_tx` ottengono skip pulito in default locale e
# CI minimal (`ci.yml` non setta l'env opt-in, vedi Batch A T-01).

_DATABASE_URL = os.getenv("DATABASE_URL")
_RUN_DB_LIVE = os.getenv("URBY_RUN_DB_LIVE_TESTS") == "1"

_SKIP_NO_DB_LIVE_OPT_IN = pytest.mark.skipif(
    not (_RUN_DB_LIVE and _DATABASE_URL),
    reason=(
        "DB-live opt-in mancante: settare URBY_RUN_DB_LIVE_TESTS=1 + "
        "DATABASE_URL con PG raggiungibile e migration applicata. "
        "Skip default per evitare access violation su psycopg2.connect "
        "in ambienti dove DATABASE_URL e' settato ma il DB non e' "
        "destinato a questi test."
    ),
)


@pytest.fixture
def pg_tx():
    """Apre una connessione PG e consegna un cursor dentro una tx che
    viene SEMPRE rollback-ata in teardown. Zero residuo nel DB.

    Assumption: la migration `2026-04-21_log_immutable_triggers.sql`
    e' gia' stata applicata (via migrations_runner al boot del backend
    in ambiente di test / staging).

    Doppio guard PRIMA di `psycopg2.connect`:
      1. env opt-in `URBY_RUN_DB_LIVE_TESTS=1` mancante -> pytest.skip.
      2. `DATABASE_URL` mancante                       -> pytest.skip.
    Solo con entrambi present arriviamo a `connect`, dove `connect_timeout=2`
    + skip su `OperationalError` chiudono lo scenario "DB temporaneamente
    irraggiungibile" senza propagare l'errore al test.
    """
    if not _RUN_DB_LIVE:
        pytest.skip(
            "DB-live test non opt-in: settare URBY_RUN_DB_LIVE_TESTS=1 "
            "per eseguire i test che fanno psycopg2.connect reale."
        )
    if not _DATABASE_URL:
        pytest.skip("DATABASE_URL non configurato")

    import psycopg2

    try:
        conn = psycopg2.connect(_DATABASE_URL, connect_timeout=2)
    except psycopg2.OperationalError as exc:
        pytest.skip(f"PG non raggiungibile per integration test: {exc}")
    conn.autocommit = False
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()
        conn.rollback()
        conn.close()


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_update_on_audit_actions_is_blocked(pg_tx) -> None:
    """UPDATE su audit_actions → RAISE EXCEPTION, tx abortita.

    Usa SAVEPOINT cosi' l'errore non aborta la tx globale del fixture
    (che deve restare viva per il rollback finale).
    """
    import psycopg2

    pg_tx.execute(
        "INSERT INTO plenora.audit_actions (action, status) "
        "VALUES (%s, %s) RETURNING id",
        ("test.immutable_update", 200),
    )
    row_id = pg_tx.fetchone()[0]

    pg_tx.execute("SAVEPOINT sp_update_attempt")
    with pytest.raises(psycopg2.Error) as excinfo:
        pg_tx.execute(
            "UPDATE plenora.audit_actions SET status = %s WHERE id = %s",
            (500, row_id),
        )
    # Ripristina lo stato della tx al savepoint per continuare ad
    # avere il cursor usabile per la pulizia.
    pg_tx.execute("ROLLBACK TO SAVEPOINT sp_update_attempt")

    assert "append-only" in str(excinfo.value).lower(), (
        f"Messaggio errore atteso 'append-only', trovato: {excinfo.value}"
    )


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_delete_on_audit_actions_without_bypass_is_blocked(pg_tx) -> None:
    """DELETE senza `SET LOCAL plenora.retention_cleanup` → blocked."""
    import psycopg2

    pg_tx.execute(
        "INSERT INTO plenora.audit_actions (action, status) "
        "VALUES (%s, %s) RETURNING id",
        ("test.immutable_delete", 200),
    )
    row_id = pg_tx.fetchone()[0]

    pg_tx.execute("SAVEPOINT sp_delete_attempt")
    with pytest.raises(psycopg2.Error) as excinfo:
        pg_tx.execute(
            "DELETE FROM plenora.audit_actions WHERE id = %s",
            (row_id,),
        )
    pg_tx.execute("ROLLBACK TO SAVEPOINT sp_delete_attempt")

    assert "retention_cleanup" in str(excinfo.value), (
        f"Messaggio errore dovrebbe citare il bypass 'retention_cleanup': "
        f"{excinfo.value}"
    )


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_delete_on_audit_actions_with_bypass_succeeds(pg_tx) -> None:
    """DELETE con `SET LOCAL plenora.retention_cleanup = 'true'` → passa.

    Simula il comportamento di un job di retention autorizzato.
    """
    pg_tx.execute(
        "INSERT INTO plenora.audit_actions (action, status) "
        "VALUES (%s, %s) RETURNING id",
        ("test.retention_bypass", 200),
    )
    row_id = pg_tx.fetchone()[0]

    # Il SET LOCAL dichiara il bypass, scopato alla transazione corrente.
    pg_tx.execute("SET LOCAL plenora.retention_cleanup = 'true'")
    pg_tx.execute("DELETE FROM plenora.audit_actions WHERE id = %s", (row_id,))

    assert pg_tx.rowcount == 1, (
        "REGRESSIONE: il bypass retention_cleanup non consente il DELETE. "
        "Il retention job legittimo e' bloccato dal trigger."
    )


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_update_with_retention_bypass_still_blocked(pg_tx) -> None:
    """Il bypass riguarda SOLO DELETE. L'immutabilita' su UPDATE e'
    totale: anche un retention job non puo' modificare una riga
    storicizzata (deve inserire una nuova riga di rettifica).
    """
    import psycopg2

    pg_tx.execute(
        "INSERT INTO plenora.audit_actions (action, status) "
        "VALUES (%s, %s) RETURNING id",
        ("test.bypass_no_update", 200),
    )
    row_id = pg_tx.fetchone()[0]

    pg_tx.execute("SET LOCAL plenora.retention_cleanup = 'true'")

    pg_tx.execute("SAVEPOINT sp_update_even_with_bypass")
    with pytest.raises(psycopg2.Error) as excinfo:
        pg_tx.execute(
            "UPDATE plenora.audit_actions SET status = %s WHERE id = %s",
            (403, row_id),
        )
    pg_tx.execute("ROLLBACK TO SAVEPOINT sp_update_even_with_bypass")

    assert "append-only" in str(excinfo.value).lower(), (
        "REGRESSIONE: il bypass retention ha reso mutabile la tabella "
        "su UPDATE. L'immutabilita' su UPDATE deve essere assoluta."
    )


@pytest.mark.integration
@_SKIP_NO_DB_LIVE_OPT_IN
def test_update_on_logs_is_blocked(pg_tx) -> None:
    """Stesso contratto anche su plenora.logs: UPDATE sempre vietato."""
    import psycopg2

    # plenora.logs ha colonne level/message/category/user_id/etc, inserisco
    # un set minimo valido. Se lo schema di logs dovesse cambiare e
    # richiedere piu' colonne NOT NULL, questo test fallirebbe sulla INSERT
    # segnalando il drift di schema (desiderabile).
    pg_tx.execute(
        "INSERT INTO plenora.logs (level, message) VALUES (%s, %s) RETURNING id",
        ("INFO", "test.logs_immutable"),
    )
    row_id = pg_tx.fetchone()[0]

    pg_tx.execute("SAVEPOINT sp_logs_update")
    with pytest.raises(psycopg2.Error) as excinfo:
        pg_tx.execute(
            "UPDATE plenora.logs SET message = %s WHERE id = %s",
            ("tampered", row_id),
        )
    pg_tx.execute("ROLLBACK TO SAVEPOINT sp_logs_update")

    assert "append-only" in str(excinfo.value).lower()
