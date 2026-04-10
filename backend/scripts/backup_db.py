"""
backup_db.py — Backup automatico dei database SQLite URBY-ETL.

Copia config.db, results.db, intermediate.db, log.db in una cartella
con timestamp. Mantiene gli ultimi N backup (default 30).

Uso:
  # Manuale:
  python scripts/backup_db.py

  # Cron (Linux, ogni notte alle 2:00):
  0 2 * * * cd /opt/urby-etl/backend && python scripts/backup_db.py

  # Task Scheduler (Windows):
  schtasks /create /tn "URBY-ETL Backup" /tr "python C:\\path\\backup_db.py" /sc daily /st 02:00

Requisiti: nessuno (solo stdlib Python).
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path

# Configurazione
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
BACKUP_ROOT = Path(__file__).resolve().parent.parent.parent / "backups"
MAX_BACKUPS = 30  # mantieni gli ultimi N backup
DB_FILES = ["config.db", "results.db", "intermediate.db", "log.db"]


def backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_ROOT / timestamp

    if not DATA_DIR.exists():
        print(f"ERRORE: DATA_DIR non trovata: {DATA_DIR}")
        sys.exit(1)

    backup_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for db_name in DB_FILES:
        src = DATA_DIR / db_name
        if src.exists():
            # SQLite safe backup: copia anche WAL e SHM se presenti
            shutil.copy2(str(src), str(backup_dir / db_name))
            for ext in ("-wal", "-shm"):
                wal = Path(str(src) + ext)
                if wal.exists():
                    shutil.copy2(str(wal), str(backup_dir / (db_name + ext)))
            size_mb = src.stat().st_size / (1024 * 1024)
            print(f"  OK: {db_name} ({size_mb:.1f} MB)")
            copied += 1
        else:
            print(f"  SKIP: {db_name} (non trovato)")

    print(f"\nBackup completato: {backup_dir} ({copied} file)")

    # Pulizia vecchi backup
    all_backups = sorted(
        [d for d in BACKUP_ROOT.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True
    )
    removed = 0
    for old_backup in all_backups[MAX_BACKUPS:]:
        shutil.rmtree(old_backup, ignore_errors=True)
        removed += 1

    if removed:
        print(f"Rimossi {removed} backup vecchi (mantenuti ultimi {MAX_BACKUPS})")


if __name__ == "__main__":
    print(f"URBY-ETL Backup — {datetime.now().isoformat()}")
    print(f"  Data dir: {DATA_DIR}")
    print(f"  Backup dir: {BACKUP_ROOT}")
    print()
    backup()
