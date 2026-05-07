"""
Estrai dal dump prod il bootstrap base per `0000_bootstrap_base_tables.sql`.

Uso: invocato manualmente, NON committato. Output:
"""

from __future__ import annotations
import re
import sys
from pathlib import Path

DUMP = Path(__file__).resolve().parent / "b2_prod_schema_dump.sql"
OUT = (
    Path(__file__).resolve().parent.parent.parent
    / "URBY-ETL" / "backend" / "migrations" / "0000_bootstrap_base_tables.sql"
)

BOOTSTRAP_TABLES = [
    # ordine: pg_dump topologico-ish; le CREATE TABLE non hanno FK inline
    # quindi ordine effettivo non vincolante per la creazione, ma manteniamo
    # ordine pg_dump per leggibilità e diff.
    "groups", "users", "projects",
    "connections", "api_connections", "arcgis_connections",
    "datasources", "file_sources", "eseguibili",
    "comparison_keys",
    "connection_shares", "api_connection_shares", "arcgis_connection_shares",
    "datasource_shares", "file_source_shares", "eseguibile_shares",
    "execution_logs", "runs", "results", "log_runs", "logs",
    "schedules", "schedule_catchup_history", "scheduler_locks", "beat_heartbeat",
    "node_meta", "node_rows",
]
assert len(BOOTSTRAP_TABLES) == 27, len(BOOTSTRAP_TABLES)
BOOTSTRAP_SET = set(BOOTSTRAP_TABLES)

BOOTSTRAP_SEQUENCES = [
    "logs_id_seq",
    "node_rows_id_seq",
    "schedule_catchup_history_id_seq",
]


def parse_dump(sql: str):
    """Ritorna (creates_by_table, pks, uniques, fks, seq_owns)."""
    # CREATE TABLE multi-line block fino a `);` su riga sola
    table_re = re.compile(
        r'CREATE TABLE "plenora"\."([a-z_]+)" \((.*?)^\);',
        re.MULTILINE | re.DOTALL,
    )
    creates = {}
    for m in table_re.finditer(sql):
        creates[m.group(1)] = m.group(0)

    # ALTER TABLE ONLY ... ADD CONSTRAINT ... fino al primo `;`
    alter_re = re.compile(
        r'ALTER TABLE ONLY "plenora"\."([a-z_]+)"\s+'
        r'ADD CONSTRAINT "([a-z_]+)" ([^;]+);',
        re.MULTILINE | re.DOTALL,
    )
    pks, uniques, fks = [], [], []
    for m in alter_re.finditer(sql):
        src, name, body = m.group(1), m.group(2), m.group(3).strip()
        body_one = re.sub(r'\s+', ' ', body)
        if 'PRIMARY KEY' in body_one:
            pks.append((src, name, body_one))
        elif body_one.startswith('UNIQUE'):
            uniques.append((src, name, body_one))
        elif body_one.startswith('FOREIGN KEY'):
            # estrai target schema
            tgt_m = re.search(
                r'REFERENCES "plenora"\."([a-z_]+)"', body_one
            )
            tgt = tgt_m.group(1) if tgt_m else None
            fks.append((src, name, body_one, tgt))

    # ALTER SEQUENCE ... OWNED BY ...
    seqown_re = re.compile(
        r'ALTER SEQUENCE "plenora"\."([a-z_]+)" OWNED BY "plenora"\."([a-z_]+)"\."([a-z_]+)";'
    )
    seq_owns = [(s, t, c) for s, t, c in seqown_re.findall(sql)]

    # ALTER TABLE ... ALTER COLUMN ... SET DEFAULT nextval(...) ...
    coldef_re = re.compile(
        r'ALTER TABLE ONLY "plenora"\."([a-z_]+)" '
        r'ALTER COLUMN "([a-z_]+)" '
        r'SET DEFAULT "?nextval"?\(\'"plenora"\."([a-z_]+)"\'(?:::"?regclass"?)?\);'
    )
    col_defaults = [(t, c, s) for t, c, s in coldef_re.findall(sql)]
    return creates, pks, uniques, fks, seq_owns, col_defaults


def rewrite_create_table(block: str) -> str:
    return block.replace(
        'CREATE TABLE "plenora"."',
        'CREATE TABLE IF NOT EXISTS "plenora"."',
        1,
    )


HEADER = """\
-- =============================================================================
-- 0000_bootstrap_base_tables.sql
--
-- Phase 1 / B-2 bootstrap PR #16 — crea le tabelle base che il codice prod
-- assume preesistenti (riferimento `pg_connection.init_db()` commento:
-- "le tabelle sono già create dallo script create_pg_schema.py" — script
-- non presente nel repo).
--
-- Questo file viene applicato da `app/db/migrations_runner.py` come prima
-- migration ordinata lessicograficamente (`0000_*` precede `2026-*`).
-- Tutte le operazioni sono idempotenti:
--   - CREATE TABLE / SEQUENCE IF NOT EXISTS (nativo PG)
--   - ALTER TABLE ADD CONSTRAINT in DO block che cattura
--     `duplicate_object`/`duplicate_table`/`undefined_table`/
--     `undefined_column`/`invalid_schema_name`
--
-- Su prod re-run (tabelle e constraint già esistenti) il file e' no-op.
-- Su DB ephemeral (CI B-2 / dev fresco) il file ricrea l'intero schema base
-- atteso dalle 15 migration successive (`2026-*.sql`).
--
-- Derivato da dump schema-only di prod (oracle ground-truth, non SQLite
-- legacy). Estratto via script locale, non committato.
--
-- Scope:
--   - 27 tabelle base (escluse tabelle create da migration successive,
--     escluso `schema_migrations` creato inline da migrations_runner.py)
--   - 3 sequence base (logs_id_seq, node_rows_id_seq,
--     schedule_catchup_history_id_seq)
--   - 27 PRIMARY KEY + 1 UNIQUE (users_username_key) + 27 FOREIGN KEY
--     bootstrap->bootstrap (zero FK verso tabelle non ancora create)
--   - 3 sequence ownership
--
-- Esclusioni esplicite:
--   - nessun INSERT/COPY/DROP/TRUNCATE/DELETE/UPDATE
--   - nessun OWNER/GRANT/REVOKE/COMMENT
--   - nessun CREATE INDEX non-PK (delegato alle migration `2026-*` che
--     usano gia' `CREATE INDEX IF NOT EXISTS`)
-- =============================================================================

"""


def emit():
    sql = DUMP.read_text(encoding="utf-8")
    creates, pks, uniques, fks, seq_owns, col_defaults = parse_dump(sql)

    # Filtra ai bootstrap
    fks_keep = [(s, n, b, t) for (s, n, b, t) in fks if s in BOOTSTRAP_SET and t in BOOTSTRAP_SET]
    pks_keep = [(s, n, b) for (s, n, b) in pks if s in BOOTSTRAP_SET]
    uniques_keep = [(s, n, b) for (s, n, b) in uniques if s in BOOTSTRAP_SET]
    seq_owns_keep = [
        (s, t, c) for (s, t, c) in seq_owns
        if s in BOOTSTRAP_SEQUENCES and t in BOOTSTRAP_SET
    ]
    col_defaults_keep = [
        (t, c, s) for (t, c, s) in col_defaults
        if t in BOOTSTRAP_SET and s in BOOTSTRAP_SEQUENCES
    ]

    # Sanity counts
    assert len(pks_keep) == 27, f"PK count mismatch: {len(pks_keep)}"
    assert len(uniques_keep) == 1, f"UNIQUE count mismatch: {len(uniques_keep)}"
    assert len(fks_keep) == 27, f"FK count mismatch: {len(fks_keep)}"
    assert len(seq_owns_keep) == 3, f"seq own count mismatch: {len(seq_owns_keep)}"
    assert len(col_defaults_keep) == 3, f"col_defaults count mismatch: {len(col_defaults_keep)}"

    # Verifica tutte le 27 tabelle bootstrap presenti
    missing = [t for t in BOOTSTRAP_TABLES if t not in creates]
    assert not missing, f"missing in dump: {missing}"

    out = [HEADER]

    out.append("-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase A: SEQUENCES (3 bootstrap)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    for seq in BOOTSTRAP_SEQUENCES:
        out.append(f'CREATE SEQUENCE IF NOT EXISTS "plenora"."{seq}"\n')
        out.append("    AS integer\n")
        out.append("    START WITH 1\n")
        out.append("    INCREMENT BY 1\n")
        out.append("    NO MINVALUE\n")
        out.append("    NO MAXVALUE\n")
        out.append("    CACHE 1;\n\n")

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase B: TABLES (27 bootstrap, no table-level constraint, only columns + DEFAULT)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    for t in BOOTSTRAP_TABLES:
        block = rewrite_create_table(creates[t])
        out.append(block + ";\n\n")

    do_pk = (
        "DO $$ BEGIN\n"
        '    ALTER TABLE ONLY "plenora"."{src}"\n'
        '        ADD CONSTRAINT "{name}" {body};\n'
        "EXCEPTION\n"
        "    WHEN duplicate_object THEN NULL;\n"
        "    WHEN duplicate_table THEN NULL;\n"
        "    WHEN invalid_schema_name THEN NULL;\n"
        "    WHEN undefined_table THEN NULL;\n"
        "END $$;\n\n"
    )

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase C: PRIMARY KEYS (27 in idempotent DO blocks)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    pks_keep_sorted = sorted(pks_keep, key=lambda x: BOOTSTRAP_TABLES.index(x[0]))
    for src, name, body in pks_keep_sorted:
        out.append(do_pk.format(src=src, name=name, body=body))

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase D: UNIQUE CONSTRAINTS (1 in idempotent DO block)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    for src, name, body in uniques_keep:
        out.append(do_pk.format(src=src, name=name, body=body))

    do_fk = (
        "DO $$ BEGIN\n"
        '    ALTER TABLE ONLY "plenora"."{src}"\n'
        '        ADD CONSTRAINT "{name}" {body};\n'
        "EXCEPTION\n"
        "    WHEN duplicate_object THEN NULL;\n"
        "    WHEN invalid_schema_name THEN NULL;\n"
        "    WHEN undefined_table THEN NULL;\n"
        "    WHEN undefined_column THEN NULL;\n"
        "END $$;\n\n"
    )

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase E: FOREIGN KEYS (27 bootstrap->bootstrap, idempotent DO blocks)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    fks_keep_sorted = sorted(fks_keep, key=lambda x: (BOOTSTRAP_TABLES.index(x[0]), x[1]))
    for src, name, body, tgt in fks_keep_sorted:
        out.append(do_fk.format(src=src, name=name, body=body))

    do_seqown = (
        "DO $$ BEGIN\n"
        '    ALTER SEQUENCE "plenora"."{seq}" OWNED BY "plenora"."{table}"."{col}";\n'
        "EXCEPTION\n"
        "    WHEN undefined_table THEN NULL;\n"
        "    WHEN undefined_column THEN NULL;\n"
        "    WHEN undefined_object THEN NULL;\n"
        "    WHEN invalid_schema_name THEN NULL;\n"
        "END $$;\n\n"
    )

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase F: SEQUENCE OWNERSHIP (3 idempotent DO blocks)\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    for seq, t, c in seq_owns_keep:
        out.append(do_seqown.format(seq=seq, table=t, col=c))

    do_coldef = (
        "DO $$ BEGIN\n"
        '    ALTER TABLE ONLY "plenora"."{table}"\n'
        '        ALTER COLUMN "{col}" SET DEFAULT nextval(\'"plenora"."{seq}"\'::regclass);\n'
        "EXCEPTION\n"
        "    WHEN undefined_table THEN NULL;\n"
        "    WHEN undefined_column THEN NULL;\n"
        "    WHEN undefined_object THEN NULL;\n"
        "    WHEN invalid_schema_name THEN NULL;\n"
        "END $$;\n\n"
    )

    out.append("\n-- ----------------------------------------------------------------------------\n")
    out.append("-- Phase G: COLUMN DEFAULTS / sequence linkage (3 idempotent DO blocks)\n")
    out.append("--          ALTER COLUMN id SET DEFAULT nextval(...) per le 3 sequence base.\n")
    out.append("--          Senza queste, INSERT senza id esplicito fallirebbe NOT NULL\n")
    out.append("--          violation. Riproduce esattamente quanto presente nel dump prod\n")
    out.append("--          (linee 797/804/811 del dump source).\n")
    out.append("-- ----------------------------------------------------------------------------\n\n")
    # Ordine deterministico per match con sequence list
    col_defaults_sorted = sorted(
        col_defaults_keep,
        key=lambda x: BOOTSTRAP_SEQUENCES.index(x[2]),
    )
    for table, col, seq in col_defaults_sorted:
        out.append(do_coldef.format(table=table, col=col, seq=seq))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(out), encoding="utf-8")
    print(f"WROTE: {OUT}")
    print(f"size:  {OUT.stat().st_size} bytes")
    print(f"lines: {len(OUT.read_text(encoding='utf-8').splitlines())}")


if __name__ == "__main__":
    emit()
    sys.exit(0)
