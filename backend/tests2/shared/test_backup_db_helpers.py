"""Smoke test per gli helper di backend/scripts/backup_db.py.

Test mirati ai soli helper puri (no subprocess/S3). I percorsi che
richiedono pg_dump / boto3 sono testati in produzione via cron.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared]


def _load_backup_module():
    """Carica backend/scripts/backup_db.py come modulo senza eseguire main()."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "backup_db.py"
    spec = importlib.util.spec_from_file_location("backup_db", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_dump_filename_format():
    """Pattern atteso: plenora_YYYYMMDD_HHMMSSZ.dump"""
    mod = _load_backup_module()
    name = mod.build_dump_filename()
    assert re.match(r"^plenora_\d{8}_\d{6}Z\.dump$", name), f"formato inatteso: {name}"


def test_build_dump_filename_is_unique_across_seconds():
    """Due chiamate in secondi diversi producono nomi diversi."""
    mod = _load_backup_module()
    # Patchiamo datetime per evitare race test
    from unittest.mock import patch
    from datetime import datetime, timezone

    with patch.object(mod, "datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 4, 17, 2, 0, 0, tzinfo=timezone.utc)
        mock_dt.side_effect = None
        n1 = mod.build_dump_filename()

        mock_dt.now.return_value = datetime(2026, 4, 17, 2, 0, 1, tzinfo=timezone.utc)
        n2 = mod.build_dump_filename()

    assert n1 != n2
    assert "20260417_020000Z" in n1
    assert "20260417_020001Z" in n2


def test_log_format_is_parseable(capsys):
    """Log line: [ISO-8601] [LEVEL] msg — parsing-friendly per cron log."""
    mod = _load_backup_module()
    mod.log("INFO", "messaggio di test")
    captured = capsys.readouterr()
    # Pattern: [2026-04-17T02:00:00Z] [INFO] messaggio di test
    assert re.match(
        r"^\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] \[INFO\] messaggio di test",
        captured.out.strip(),
    )


def test_require_env_raises_on_missing(monkeypatch):
    mod = _load_backup_module()
    monkeypatch.delenv("TEST_BACKUP_MISSING_VAR", raising=False)
    with pytest.raises(SystemExit) as exc:
        mod.require_env("TEST_BACKUP_MISSING_VAR")
    assert exc.value.code == 4


def test_require_env_returns_value(monkeypatch):
    mod = _load_backup_module()
    monkeypatch.setenv("TEST_BACKUP_PRESENT_VAR", "postgresql://x/y")
    assert mod.require_env("TEST_BACKUP_PRESENT_VAR") == "postgresql://x/y"
