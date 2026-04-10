"""
encrypt_existing_secrets.py
===========================
Migrazione one-shot per cifrare i campi sensibili dei record già esistenti
in SQLite (data/config.db) usando il modulo ``app.shared.crypto``.

Cosa fa
-------
Per ogni tabella con credenziali at-rest:

  1. ``connections``        — campo ``password``           (DB connections)
  2. ``api_connections``    — config.auth.{password,
                                            client_secret,
                                            token,
                                            access_token,
                                            refresh_token,
                                            key_value}     (API connections)
  3. ``datasources``        — config.{password,
                                       private_key,
                                       private_key_passphrase,
                                       client_secret}      (FTP/SFTP/SharePoint)

Lo script:

* è **idempotente**: i valori già cifrati (con prefisso ``enc:``) vengono
  saltati. Puoi rilanciarlo dopo nuovi insert o dopo deploy senza problemi.
* fa un **dry-run** opzionale (``--dry-run``) che mostra cosa verrebbe
  modificato senza scrivere nel DB.
* fa un **backup automatico** del file ``config.db`` prima di toccarlo
  (suffisso ``.bak.YYYYMMDD_HHMMSS``).

⚠️  PRIMA DI LANCIARE
--------------------
1. Assicurati di aver impostato ``URBY_ENCRYPTION_KEY`` (o ``CONN_ENCRYPT_KEY``
   o ``SECRET_KEY``) nell'ambiente da cui lanci lo script. La chiave **deve
   essere la stessa** che userà l'app in runtime, altrimenti dopo la migrazione
   non potrai più decifrare nulla.
2. Fai un backup esterno della cartella ``data/`` (oltre a quello automatico).
3. Lancia con ``--dry-run`` la prima volta per vedere il piano di migrazione.

Uso
---
    # Da backend/
    python -m scripts.encrypt_existing_secrets --dry-run
    python -m scripts.encrypt_existing_secrets             # esecuzione reale
    python -m scripts.encrypt_existing_secrets --no-backup # skip backup db
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable, Tuple

# Aggiungi backend/ al sys.path se lanciato come script standalone
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Carica backend/.env se presente, così l'utente non deve esportare a mano
# URBY_ENCRYPTION_KEY in PowerShell/cmd prima di lanciare lo script.
try:
    from dotenv import load_dotenv
    _ENV_FILE = _BACKEND_ROOT / ".env"
    if _ENV_FILE.exists():
        load_dotenv(_ENV_FILE)
        print(f"[INFO] Caricato {_ENV_FILE}")
except ImportError:
    pass

from app.shared.crypto import encrypt, is_encrypted  # noqa: E402

# Lista campi cifrati (deve restare in sync con i moduli che li gestiscono)
_CONN_PWD_FIELD = "password"
_API_AUTH_FIELDS = (
    "password",
    "client_secret",
    "token",
    "access_token",
    "refresh_token",
    "key_value",
)
_DS_FIELDS = (
    "password",
    "private_key",
    "private_key_passphrase",
    "client_secret",
)


def _print_header(title: str) -> None:
    print()
    print("-" * 70)
    print(f"  {title}")
    print("-" * 70)


def _backup_db(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.with_suffix(f".db.bak.{ts}")
    shutil.copy2(db_path, backup_path)
    print(f"[OK] Backup creato: {backup_path}")
    return backup_path


def _migrate_connections(dry_run: bool) -> Tuple[int, int]:
    """Cifra le password nelle DB connections. Ritorna (visited, encrypted)."""
    import app.db.config_db_service as cfg_db

    _print_header("TABELLA: connections (DB connections)")
    rows = cfg_db.fetchall("SELECT id, name, password FROM connections")
    visited = encrypted = 0
    for row in rows:
        visited += 1
        pwd = row.get("password")
        if not pwd or is_encrypted(pwd):
            print(f"  -{row['id'][:40]:40s}  (skip: empty o già cifrata)")
            continue
        new_pwd = encrypt(pwd)
        encrypted += 1
        if dry_run:
            print(f"  ->{row['id'][:40]:40s}  WOULD ENCRYPT password")
        else:
            cfg_db.execute(
                "UPDATE connections SET password = ? WHERE id = ?",
                (new_pwd, row["id"]),
            )
            print(f"  [OK]{row['id'][:40]:40s}  encrypted password")
    print(f"  ->{encrypted}/{visited} record cifrati")
    return visited, encrypted


def _migrate_api_connections(dry_run: bool) -> Tuple[int, int]:
    """Cifra i campi auth.* nei JSON config delle API connections."""
    import app.db.config_db_service as cfg_db

    _print_header("TABELLA: api_connections (API enrichment)")
    rows = cfg_db.fetchall("SELECT id, config FROM api_connections")
    visited = encrypted_records = 0
    for row in rows:
        visited += 1
        cfg_str = row.get("config")
        if not cfg_str:
            continue
        try:
            cfg = json.loads(cfg_str) if isinstance(cfg_str, str) else cfg_str
        except json.JSONDecodeError as e:
            print(f"  [ERR]{row['id']}  config JSON malformato: {e}")
            continue

        auth = cfg.get("auth") or {}
        if not isinstance(auth, dict):
            print(f"  -{row['id']}  (skip: auth non è dict)")
            continue

        changed_fields = []
        for field in _API_AUTH_FIELDS:
            val = auth.get(field)
            if isinstance(val, str) and val and not is_encrypted(val):
                auth[field] = encrypt(val)
                changed_fields.append(field)

        if not changed_fields:
            print(f"  -{row['id']:30s}  (skip: nulla da cifrare)")
            continue

        encrypted_records += 1
        if dry_run:
            print(f"  ->{row['id']:30s}  WOULD ENCRYPT: {', '.join(changed_fields)}")
        else:
            cfg_db.execute(
                "UPDATE api_connections SET config = ? WHERE id = ?",
                (json.dumps(cfg), row["id"]),
            )
            print(f"  [OK]{row['id']:30s}  encrypted: {', '.join(changed_fields)}")
    print(f"  ->{encrypted_records}/{visited} record con campi cifrati")
    return visited, encrypted_records


def _migrate_datasources(dry_run: bool) -> Tuple[int, int]:
    """Cifra i campi sensibili nei JSON config dei datasources."""
    import app.db.config_db_service as cfg_db

    _print_header("TABELLA: datasources (FTP/SFTP/SharePoint)")
    rows = cfg_db.fetchall("SELECT id, config FROM datasources")
    visited = encrypted_records = 0
    for row in rows:
        visited += 1
        cfg_str = row.get("config")
        if not cfg_str:
            continue
        try:
            cfg = json.loads(cfg_str) if isinstance(cfg_str, str) else cfg_str
        except json.JSONDecodeError as e:
            print(f"  [ERR]{row['id']}  config JSON malformato: {e}")
            continue

        changed_fields = []
        for field in _DS_FIELDS:
            val = cfg.get(field)
            if isinstance(val, str) and val and not is_encrypted(val):
                cfg[field] = encrypt(val)
                changed_fields.append(field)

        if not changed_fields:
            print(f"  -{row['id']:50s}  (skip: nulla da cifrare)")
            continue

        encrypted_records += 1
        if dry_run:
            print(f"  ->{row['id']:50s}  WOULD ENCRYPT: {', '.join(changed_fields)}")
        else:
            cfg_db.execute(
                "UPDATE datasources SET config = ? WHERE id = ?",
                (json.dumps(cfg), row["id"]),
            )
            print(f"  [OK]{row['id']:50s}  encrypted: {', '.join(changed_fields)}")
    print(f"  ->{encrypted_records}/{visited} record con campi cifrati")
    return visited, encrypted_records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Mostra cosa verrebbe modificato senza scrivere")
    parser.add_argument("--no-backup", action="store_true", help="Skip del backup automatico di config.db")
    args = parser.parse_args()

    # Verifica chiave: se manca, il modulo crypto stamperà un warning ma userà
    # la chiave dev di default → ESCI con errore esplicito perché in migrazione
    # è MOLTO pericoloso usare la chiave sbagliata.
    import os
    if not any(os.getenv(k) for k in ("URBY_ENCRYPTION_KEY", "CONN_ENCRYPT_KEY", "SECRET_KEY", "JWT_SECRET_KEY")):
        print("[ERR] ERRORE: nessuna chiave di cifratura nelle env var.", file=sys.stderr)
        print("  Imposta URBY_ENCRYPTION_KEY (o CONN_ENCRYPT_KEY / SECRET_KEY) prima di lanciare.", file=sys.stderr)
        print("  Deve essere la stessa chiave che userà l'app in runtime.", file=sys.stderr)
        return 2

    print("=" * 70)
    print("  URBY-ETL - Migrazione cifratura segreti at-rest")
    print(f"  Mode: {'DRY-RUN (nessuna scrittura)' if args.dry_run else 'EXECUTE'}")
    print("=" * 70)

    # Backup del DB
    from app.config import settings
    db_path = Path(settings.DATA_DIR) / "config.db"
    if not db_path.exists():
        print(f"✗ DB non trovato: {db_path}", file=sys.stderr)
        return 1

    if not args.dry_run and not args.no_backup:
        _backup_db(db_path)
    elif args.dry_run:
        print(f"  (dry-run: nessun backup)")
    else:
        print(f"  (--no-backup: skip backup)")

    totals = []
    for fn in (_migrate_connections, _migrate_api_connections, _migrate_datasources):
        try:
            totals.append(fn(args.dry_run))
        except Exception as e:
            print(f"\n[ERR] ERRORE in {fn.__name__}: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
            return 1

    _print_header("RIEPILOGO")
    visited_total = sum(t[0] for t in totals)
    encrypted_total = sum(t[1] for t in totals)
    print(f"  Record visitati: {visited_total}")
    print(f"  Record con campi cifrati: {encrypted_total}")
    if args.dry_run:
        print()
        print("  Per eseguire davvero, rilancia SENZA --dry-run.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
