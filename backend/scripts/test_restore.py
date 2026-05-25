"""Verifica restore settimanale del backup su DB usa-e-getta."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse, urlunparse


# Standalone script: add backend root so `app.*` imports work from cron/systemd.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


SCHEMA = os.getenv("BACKUP_SCHEMA", "plenora")

# Tabelle core dello schema applicativo. Verifica solo che siano queryabili
# (schema restored, tipi corretti). Non assertiamo count > 0 perche' un
# sistema fresh puo' avere 0 progetti / 0 schedule.
SANITY_TABLES = ["users", "projects", "connections", "schedules"]

PG_RESTORE_TIMEOUT_SEC = int(os.getenv("BACKUP_RESTORE_TIMEOUT_SEC", "3600"))

# Safety: il test DB name matcha solo questo pattern. Qualsiasi cosa fuori
# non viene droppata (difesa contro typo/injection nell'env var).
TEST_DB_PATTERN = re.compile(r"^plenora_restore_test_\d{8}_\d{6}$")



_LOG_BUFFER: list[str] = []
_LOG_TAIL_LINES = 30


def log(level: str, msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    _LOG_BUFFER.append(line)


# Tabella sorella di backup_runs con shape
# ridotta (niente bucket_key/size: il verify non produce output persistente).


def _db_connect():
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None
    try:
        import psycopg
    except ImportError:
        log("WARN", "psycopg non disponibile: verify run tracking DB disabilitato")
        return None
    try:
        return psycopg.connect(db_url)
    except Exception as e:
        log("WARN", f"db connect fallito (non-fatal): {type(e).__name__}: {e}")
        return None


def db_insert_verify_start():
    conn = _db_connect()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO plenora.verify_runs (started_at, result) "
                    "VALUES (now(), 'running') RETURNING id"
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
    except Exception as e:
        log("WARN", f"db_insert_verify_start fallito (non-fatal): {e}")
        return None
    finally:
        conn.close()


def db_update_verify_end(run_id, result: str, exit_code: int,
                          error_message=None) -> None:
    if run_id is None:
        return
    conn = _db_connect()
    if conn is None:
        return
    try:
        log_tail = "\n".join(_LOG_BUFFER[-_LOG_TAIL_LINES:])
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE plenora.verify_runs SET finished_at=now(), result=%s, "
                    "exit_code=%s, log_tail=%s, error_message=%s WHERE id=%s",
                    (result, exit_code, log_tail, error_message, run_id),
                )
    except Exception as e:
        log("WARN", f"db_update_verify_end fallito (non-fatal): {e}")
    finally:
        conn.close()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        log("ERROR", f"Env var '{name}' non configurata.")
        sys.exit(4)
    return value


def derive_test_db_name() -> str:
    """Nome DB test: plenora_restore_test_<YYYYMMDD_HHMMSS> (solo ASCII safe)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = f"plenora_restore_test_{ts}"
    assert TEST_DB_PATTERN.match(name), f"DB name non matcha pattern: {name}"
    return name


def split_db_url(url: str) -> Tuple[str, str]:
    """Restituisce (admin_url_verso_postgres_db, db_name_originale).

    Per CREATE/DROP DATABASE serve connettersi a un DB diverso da quello
    target. Default: 'postgres' (sempre presente sui cluster standard).
    """
    parsed = urlparse(url)
    original_db = parsed.path.lstrip("/") or "postgres"
    admin = urlunparse(parsed._replace(path="/postgres"))
    return admin, original_db


def make_target_url(admin_url: str, test_db: str) -> str:
    """Sostituisce il path /postgres con /<test_db> nell'admin url."""
    parsed = urlparse(admin_url)
    return urlunparse(parsed._replace(path=f"/{test_db}"))



def _storage():
    """Lazy import + factory del backend object storage (backup bucket)."""
    try:
        from app.infrastructure.storage.cloud_backends import get_backup_object_storage
    except ImportError as e:
        log("ERROR", f"cloud_backends import fallito: {e}")
        sys.exit(4)
    return get_backup_object_storage()


def latest_dump_key() -> str:
    """Piu' recente .dump nel bucket con prefix `plenora_`."""
    storage = _storage()
    try:
        objs = storage.list_objects(prefix="plenora_")
    except Exception as e:
        log("ERROR", f"list_objects fallito: {type(e).__name__}: {e}")
        sys.exit(3)

    dumps = [o for o in objs if o["key"].endswith(".dump")]
    if not dumps:
        log("ERROR", "Nessun dump plenora_*.dump trovato nel backend storage")
        sys.exit(2)

    latest = max(dumps, key=lambda o: o["last_modified"])
    log("INFO", f"dump selezionato: {latest['key']} ({latest['last_modified']})")
    return latest["key"]


def download_dump(key: str, dest: Path) -> None:
    storage = _storage()
    try:
        storage.download(key, dest)
    except Exception as e:
        log("ERROR", f"download fallito: {type(e).__name__}: {e}")
        sys.exit(3)

    size_mb = dest.stat().st_size / (1024 * 1024)
    log("INFO", f"download ok: {dest.name} ({size_mb:.1f} MB)")



def _run_psql(url: str, sql: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-c", sql],
        capture_output=True, text=True, timeout=timeout,
    )


def createdb(admin_url: str, test_db: str) -> None:
    if not TEST_DB_PATTERN.match(test_db):
        raise ValueError(f"test_db name non matcha pattern safe: {test_db}")
    # Uso ident double-quoted. Il nome e' whitelisted dal pattern regex
    # (solo [a-z0-9_]), quindi l'identifier e' safe.
    result = _run_psql(admin_url, f'CREATE DATABASE "{test_db}"')
    if result.returncode != 0:
        raise RuntimeError(f"CREATE DATABASE fallito: {result.stderr.strip()[:500]}")
    log("INFO", f"creato test DB: {test_db}")


def dropdb(admin_url: str, test_db: str) -> None:
    """Drop best-effort — non solleva su errore (cleanup garantito)."""
    if not TEST_DB_PATTERN.match(test_db):
        log("WARN", f"dropdb skippato: {test_db} non matcha pattern safe")
        return
    try:
        result = _run_psql(
            admin_url,
            f'DROP DATABASE IF EXISTS "{test_db}" WITH (FORCE)',
            timeout=120,
        )
        if result.returncode != 0:
            # Retry senza FORCE: vecchi Postgres <13 non supportano WITH (FORCE).
            result = _run_psql(
                admin_url, f'DROP DATABASE IF EXISTS "{test_db}"', timeout=120,
            )
        if result.returncode != 0:
            log("WARN", f"dropdb {test_db} fallito (non-fatal): {result.stderr.strip()[:300]}")
        else:
            log("INFO", f"droppato test DB: {test_db}")
    except Exception as e:
        log("WARN", f"dropdb {test_db} eccezione (non-fatal): {e}")



def pg_restore(target_url: str, dump_file: Path) -> None:
    cmd = [
        "pg_restore",
        "--dbname", target_url,
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        str(dump_file),
    ]
    log("INFO", f"pg_restore start -> {target_url.rsplit('/', 1)[-1]}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=PG_RESTORE_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"pg_restore timeout dopo {PG_RESTORE_TIMEOUT_SEC}s")
    except FileNotFoundError:
        log("ERROR", "pg_restore non trovato nel PATH.")
        sys.exit(4)

    if result.returncode != 0:
        raise RuntimeError(f"pg_restore exit {result.returncode}: {result.stderr.strip()[:500]}")
    log("INFO", "pg_restore ok")



def sanity_check_tables(target_url: str) -> None:
    for table in SANITY_TABLES:
        # schema/table entrambi controllati da env/costante — no injection.
        sql = f'SELECT COUNT(*) FROM "{SCHEMA}"."{table}"'
        result = subprocess.run(
            ["psql", target_url, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"sanity query su {SCHEMA}.{table} fallita: "
                f"{result.stderr.strip()[:300]}"
            )
        try:
            count = int(result.stdout.strip())
        except ValueError:
            raise RuntimeError(
                f"sanity query su {SCHEMA}.{table}: output non numerico "
                f"'{result.stdout.strip()[:50]}'"
            )
        log("INFO", f"  {SCHEMA}.{table}: {count} rows")
    log("INFO", f"sanity ok su {len(SANITY_TABLES)} tabelle")



def main() -> None:
    log("INFO", "test_restore start")
    db_url = require_env("DATABASE_URL")
    admin_url, _ = split_db_url(db_url)
    test_db = derive_test_db_name()
    target_url = make_target_url(admin_url, test_db)

    key = latest_dump_key()

    tmp_dir = Path(tempfile.mkdtemp(prefix="plenora_restore_test_"))
    dump_file = tmp_dir / "dump"
    db_created = False

    run_id = db_insert_verify_start()
    final_result = "failed"
    final_exit_code = 1
    final_error = None

    try:
        download_dump(key, dump_file)

        createdb(admin_url, test_db)
        db_created = True

        pg_restore(target_url, dump_file)
        sanity_check_tables(target_url)
        log("INFO", "test_restore completato con successo")
        final_result = "success"
        final_exit_code = 0

    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else 1
        final_exit_code = code
        final_error = _LOG_BUFFER[-1] if _LOG_BUFFER else f"exit {code}"
        raise
    except Exception as e:
        final_error = f"{type(e).__name__}: {e}"
        log("ERROR", f"test_restore fallito: {final_error}")
        final_exit_code = 1
        raise SystemExit(1)

    finally:
        if db_created:
            try:
                dropdb(admin_url, test_db)
            except Exception as e:
                log("WARN", f"dropdb fallito (non-fatal): {e}")
        try:
            if dump_file.exists():
                dump_file.unlink()
            tmp_dir.rmdir()
        except OSError as e:
            log("WARN", f"cleanup temp dir fallito (non-fatal): {e}")
        db_update_verify_end(
            run_id=run_id,
            result=final_result,
            exit_code=final_exit_code,
            error_message=final_error,
        )


if __name__ == "__main__":
    main()
