"""Gestione backup del DB — feature admin per superadmin.

Espone via REST:
  - lista backup su S3
  - stato ultimo run (da tabella DB `plenora.backup_runs`)
  - schedule info (metadati statici da env var)
  - trigger manuale on-demand

Il backup reale (pg_dump + upload S3) e' orchestrato da
backend/scripts/backup_db.py, invocato sia dal systemd timer
plenora-backup.timer (daily 02:00 UTC) sia dall'endpoint trigger.
A fine run lo script scrive una row in `plenora.backup_runs`
con esito/exit code/log tail (container-safe: niente journalctl).
"""
