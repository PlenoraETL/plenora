"""
Migra dati da SQLite config.db → PostgreSQL plenora schema.
Solo config data (no results blob, no intermediate runs, no logs).
"""
import sqlite3
import json
import psycopg2
import psycopg2.extras

SQLITE_PATH = "C:/Users/Marco/Desktop/int_efm/URBY-ETL/backend/data/config.db"
PG_DSN = "postgresql://postgres:zAWMyAJTzEnDUOj9JyMKdmngZWrhlupyo94GGPOOT1o%3D@15.161.59.22:5432/plenora"
PG_SCHEMA = "plenora"

# Tabelle da migrare in ORDINE (rispetta FK)
TABLES = [
    "groups",
    "users",
    "projects",
    "connections",
    "file_sources",
    "datasources",
    "arcgis_connections",
    "api_connections",
    "schedules",
    "eseguibili",
    "comparison_keys",
    "app_settings",
    "beat_heartbeat",
    # Shares
    "connection_shares",
    "datasource_shares",
    "eseguibile_shares",
    "api_connection_shares",
    "arcgis_connection_shares",
    "file_source_shares",
]

# Colonne booleane che in SQLite sono INTEGER 0/1
BOOLEAN_COLS = {"is_active", "is_enabled", "pinned"}

# Colonne BLOB che vanno come bytes
BLOB_COLS = {"data"}


def migrate():
    # Connessioni
    sq = sqlite3.connect(SQLITE_PATH)
    sq.row_factory = sqlite3.Row

    pg = psycopg2.connect(PG_DSN, options=f"-c search_path={PG_SCHEMA}")
    pg.autocommit = False
    pgcur = pg.cursor()
    pgcur.execute(f"SET search_path TO {PG_SCHEMA}")

    total = 0

    # Disabilita FK constraint per la durata dell'import
    # (PostgreSQL: session-level, non globale)
    pgcur.execute("SET session_replication_role = 'replica'")
    pg.commit()

    for table in TABLES:
        try:
            rows = sq.execute(f"SELECT * FROM {table}").fetchall()
        except Exception as e:
            print(f"  SKIP {table}: {e}")
            continue

        if not rows:
            print(f"  {table}: 0 righe (skip)")
            continue

        # Pulisci tabella PG prima dell'import (per idempotenza)
        try:
            pgcur.execute(f"DELETE FROM {table}")
        except Exception:
            pg.rollback()
            pgcur.execute(f"SET search_path TO {PG_SCHEMA}")
            continue

        cols = rows[0].keys()
        placeholders = ", ".join(["%s"] * len(cols))
        col_names = ", ".join(cols)
        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

        count = 0
        for row in rows:
            values = []
            for col in cols:
                val = row[col]
                # Converti booleani SQLite (0/1) → Python bool
                if col in BOOLEAN_COLS and val is not None:
                    val = bool(val)
                # BLOB: assicura bytes
                if col in BLOB_COLS and val is not None:
                    val = bytes(val) if not isinstance(val, bytes) else val
                values.append(val)

            try:
                pgcur.execute(insert_sql, tuple(values))
                count += 1
            except Exception as e:
                pg.rollback()
                pgcur.execute(f"SET search_path TO {PG_SCHEMA}")
                print(f"  ERROR {table} row: {str(e)[:100]}")
                break

        pg.commit()
        total += count
        print(f"  {table}: {count}/{len(rows)} righe migrated")

    # Riabilita FK constraint
    pgcur.execute("SET session_replication_role = 'origin'")
    pg.commit()

    sq.close()
    pg.close()
    print(f"\nTotale: {total} righe migrate su PostgreSQL")


if __name__ == "__main__":
    print("Migrazione SQLite → PostgreSQL\n")
    migrate()
