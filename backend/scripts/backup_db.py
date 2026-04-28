"""backup_db.py — Backup PostgreSQL PLENORA-ETL su AWS S3.

Scope: dump dello schema applicativo (default: `plenora`, ~20 MB) — non
dell'intero DB. Il DB puo' contenere altri schemi con dati di dominio
(catasto, aste, OMI, ecc. — decine di GB rigenerabili da sorgenti pubbliche)
che NON hanno senso nel backup giornaliero.

Prerequisiti per il restore:
  - DB target con estensioni installate in public: postgis, h3, h3_postgis,
    postgis_raster, postgis_topology (in topology schema)
  - Lo schema `plenora` e' self-contained: zero FK cross-schema verso
    aste/catasto/login/ecc. (verificato 2026-04-17)

Flusso:
  1. pg_dump --schema=plenora (formato custom, compresso) -> file temporaneo
  2. pg_restore --list sul dump -> verifica integrita' del TOC
  3. Upload del dump su S3 via boto3 (credenziali via IAM role EC2)
  4. Cleanup del file locale
  5. Exit code 0 se tutto ok, 1 su errore (cron decide cosa farne)

Retention: gestita lato AWS via lifecycle rule sul bucket, non da questo
script. Transition a Standard-IA dopo 30 giorni, delete dopo 365 giorni.

Requisiti sul server di produzione:
  - `pg_dump` e `pg_restore` binari nel PATH, versione >= server Postgres
    (su Ubuntu 24.04 apt installa la 16.x; se server e' 17.x serve il
    repo PGDG: postgresql-client-17)
  - Env var DATABASE_URL (formato: postgresql://user:pass@host:port/db)
    letta dal .env via systemd unit wrapper
  - IAM role attached all'istanza EC2 con permessi s3:PutObject + s3:GetObject
    + s3:ListBucket sul bucket target
  - Python deps nel venv backend: boto3

Schedulazione produzione: systemd timer `plenora-backup.timer` (daily
02:00 UTC + RandomizedDelaySec=300). Unit files in /etc/systemd/system/:
  - plenora-backup.service  (oneshot che esegue questo script)
  - plenora-backup.timer    (OnCalendar=*-*-* 02:00:00, Persistent=true)

Uso manuale:
  # Da chiamante interattivo (CWD = backend/):
  DATABASE_URL=... python3 scripts/backup_db.py

  # Tramite systemd (produzione):
  sudo systemctl start plenora-backup.service
  sudo journalctl -u plenora-backup --since '1 hour ago'

  # Via API (solo superadmin):
  POST /api/admin/backups/trigger

Exit codes:
    0 success
    1 pg_dump failed
    2 integrity check failed
    3 S3 upload failed
    4 environment/config error
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Script standalone: aggiungi backend root a sys.path per `app.shared.*` imports.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# ── Configurazione ──────────────────────────────────────────────────────────

# Scope del dump: default schema applicativo 'plenora'. Vuoto ("") -> intero DB.
BACKUP_SCHEMA = os.getenv("BACKUP_SCHEMA", "plenora")
PG_DUMP_TIMEOUT_SEC = int(os.getenv("BACKUP_PG_DUMP_TIMEOUT_SEC", "3600"))

# Buffer per log_tail persistito su DB a fine run (ultime N righe per diagnostic).
_LOG_BUFFER: list[str] = []
_LOG_TAIL_LINES = 30


# ── Helpers ─────────────────────────────────────────────────────────────────


def log(level: str, msg: str) -> None:
    """Log line su stdout con timestamp ISO-8601 UTC + append a buffer.

    Il buffer viene persistito su plenora.backup_runs.log_tail a fine run per
    dare alla dashboard admin le ultime 30 righe diagnostiche senza dover
    leggere journalctl (container-friendly).
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{ts}] [{level}] {msg}"
    print(line, flush=True)
    _LOG_BUFFER.append(line)


# ── DB run tracking (plenora.backup_runs) ────────────────────────────────────
# Scopo: sostituire l'osservabilita' basata su systemctl/journalctl (host-local)
# con una fonte DB-based leggibile da qualsiasi istanza backend (container-safe).


def _db_connect():
    """Connessione Postgres standalone via DATABASE_URL.
    Ritorna None se DATABASE_URL assente, psycopg2 non installato o errore di
    connessione. Il caller deve essere tollerante: il DB logging e' best-effort,
    non deve mai far fallire il backup.
    """
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        return None
    try:
        import psycopg2
    except ImportError:
        log("WARN", "psycopg2 non disponibile: run tracking DB disabilitato")
        return None
    try:
        return psycopg2.connect(db_url)
    except Exception as e:
        log("WARN", f"db connect fallito (non-fatal): {type(e).__name__}: {e}")
        return None


def db_insert_run_start() -> Optional[int]:
    """INSERT row con result='running', ritorna id. None se DB non raggiungibile."""
    conn = _db_connect()
    if conn is None:
        return None
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO plenora.backup_runs (started_at, result) "
                    "VALUES (now(), 'running') RETURNING id"
                )
                row = cur.fetchone()
                return int(row[0]) if row else None
    except Exception as e:
        log("WARN", f"db_insert_run_start fallito (non-fatal): {e}")
        return None
    finally:
        conn.close()


def db_update_run_end(
    run_id: Optional[int],
    result: str,
    exit_code: int,
    bucket_key: Optional[str] = None,
    size_bytes: Optional[int] = None,
    error_message: Optional[str] = None,
) -> None:
    """UPDATE row con stato finale + log_tail. No-op se run_id e' None."""
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
                    "UPDATE plenora.backup_runs SET finished_at=now(), result=%s, "
                    "exit_code=%s, bucket_key=%s, size_bytes=%s, log_tail=%s, "
                    "error_message=%s WHERE id=%s",
                    (result, exit_code, bucket_key, size_bytes, log_tail,
                     error_message, run_id),
                )
    except Exception as e:
        log("WARN", f"db_update_run_end fallito (non-fatal): {e}")
    finally:
        conn.close()


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        log("ERROR", f"Env var '{name}' non configurata.")
        sys.exit(4)
    return value


def build_dump_filename() -> str:
    """Nome file dump con timestamp UTC: plenora_20260418_020000Z.dump"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")
    return f"plenora_{ts}.dump"


# ── Step 1: pg_dump ─────────────────────────────────────────────────────────


def run_pg_dump(database_url: str, out_file: Path) -> None:
    """Esegue pg_dump in formato custom con compressione massima.

    Formato custom (.dump) supporta pg_restore selettivo, parallel restore,
    e compressione integrata (zlib). Piu' piccolo e piu' robusto di SQL plain.
    """
    cmd = [
        "pg_dump",
        "--dbname", database_url,
        "--format=custom",
        "--compress=9",
        "--no-owner",          # restore indipendente da utente proprietario
        "--no-privileges",     # restore indipendente dai GRANT originali
    ]
    if BACKUP_SCHEMA:
        cmd.extend(["--schema", BACKUP_SCHEMA])
    cmd.extend(["--file", str(out_file)])
    scope = f"schema={BACKUP_SCHEMA}" if BACKUP_SCHEMA else "intero DB"
    log("INFO", f"pg_dump start ({scope}) -> {out_file.name}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=PG_DUMP_TIMEOUT_SEC,
        )
    except subprocess.TimeoutExpired:
        log("ERROR", f"pg_dump timeout dopo {PG_DUMP_TIMEOUT_SEC}s")
        sys.exit(1)
    except FileNotFoundError:
        log("ERROR", "pg_dump binario non trovato nel PATH. Installa postgresql-client.")
        sys.exit(4)

    if result.returncode != 0:
        log("ERROR", f"pg_dump fallito (exit {result.returncode}): {result.stderr.strip()[:500]}")
        sys.exit(1)

    size_mb = out_file.stat().st_size / (1024 * 1024)
    log("INFO", f"pg_dump ok: {size_mb:.1f} MB")


# ── Step 2: verifica integrita' del dump ────────────────────────────────────


def verify_dump_integrity(dump_file: Path) -> None:
    """pg_restore --list legge il TOC del dump e lo stampa.

    Non crea database, non carica dati. Verifica che il dump sia parsabile
    e strutturalmente valido. Rileva dump troncati o corrotti prima dell'upload.
    """
    log("INFO", "verifica integrita' dump (pg_restore --list)...")
    try:
        result = subprocess.run(
            ["pg_restore", "--list", str(dump_file)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        log("ERROR", "pg_restore --list timeout")
        sys.exit(2)

    if result.returncode != 0:
        log("ERROR", f"dump corrotto (pg_restore --list exit {result.returncode}): {result.stderr.strip()[:500]}")
        sys.exit(2)

    # Conta entry nel TOC (sanity: un dump valido ne ha decine/centinaia)
    toc_lines = [l for l in result.stdout.splitlines() if l and not l.startswith(";")]
    if len(toc_lines) < 5:
        log("ERROR", f"dump sospetto: solo {len(toc_lines)} entry nel TOC")
        sys.exit(2)

    log("INFO", f"integrita' ok: {len(toc_lines)} entry nel TOC")


# ── Step 3: upload S3 ───────────────────────────────────────────────────────


def upload_to_storage(dump_file: Path, key: str) -> None:
    """Upload via ObjectStorage backend (cloud-agnostic; default AWS S3).

    Backend scelto dalla factory in app.shared.cloud_backends basata su
    CLOUD_PROVIDER env. Credenziali risolte dal backend (IAM role EC2
    per AWS, Managed Identity per Azure, SA per GCP).
    """
    try:
        from app.shared.cloud_backends import get_backup_object_storage
    except ImportError as e:
        log("ERROR", f"cloud_backends import fallito: {e}")
        sys.exit(4)

    provider = os.getenv("CLOUD_PROVIDER", "aws")
    log("INFO", f"upload [{provider}] key={key}...")
    try:
        storage = get_backup_object_storage()
        storage.upload(dump_file, key)
    except Exception as e:
        log("ERROR", f"Upload fallito: {type(e).__name__}: {e}")
        sys.exit(3)

    size_mb = dump_file.stat().st_size / (1024 * 1024)
    log("INFO", f"upload ok: {key} ({size_mb:.1f} MB)")


# ── Main ────────────────────────────────────────────────────────────────────


def main() -> None:
    log("INFO", "PLENORA-ETL backup start")
    database_url = require_env("DATABASE_URL")

    dump_name = build_dump_filename()
    tmp_dir = Path(tempfile.mkdtemp(prefix="plenora_backup_"))
    dump_file = tmp_dir / dump_name

    run_id = db_insert_run_start()
    final_result = "failed"
    final_exit_code = 1
    final_error: Optional[str] = None
    uploaded_key: Optional[str] = None
    uploaded_size: Optional[int] = None

    try:
        run_pg_dump(database_url, dump_file)
        verify_dump_integrity(dump_file)
        uploaded_size = dump_file.stat().st_size
        upload_to_storage(dump_file, dump_name)
        uploaded_key = dump_name
        final_result = "success"
        final_exit_code = 0
        log("INFO", "backup completato con successo")
    except SystemExit as e:
        # Le funzioni run_pg_dump/verify/upload usano sys.exit(N). Catturiamo
        # il codice, persistiamo su DB, poi ri-solleviamo a fine finally.
        code = e.code if isinstance(e.code, int) else 1
        final_exit_code = code
        final_error = _LOG_BUFFER[-1] if _LOG_BUFFER else f"exit {code}"
        raise
    except Exception as e:
        final_error = f"{type(e).__name__}: {e}"
        log("ERROR", f"eccezione non gestita: {final_error}")
        raise
    finally:
        try:
            if dump_file.exists():
                dump_file.unlink()
            tmp_dir.rmdir()
        except OSError as e:
            log("WARN", f"cleanup temp dir fallito (non-fatal): {e}")
        # DB update dopo cleanup cosi' il log_tail include eventuali messaggi di
        # cleanup failure.
        db_update_run_end(
            run_id=run_id,
            result=final_result,
            exit_code=final_exit_code,
            bucket_key=uploaded_key,
            size_bytes=uploaded_size,
            error_message=final_error,
        )


if __name__ == "__main__":
    main()
