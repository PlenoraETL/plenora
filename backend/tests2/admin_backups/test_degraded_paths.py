"""
Contract test — graceful degradation di admin_backups/service.

Due superfici:
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# list_backups — path degraded (boto3/S3)


def test_list_backups_when_boto3_missing_returns_empty():
    """ImportError su boto3 → [] (non solleva)."""
    from app.core.admin_backups import service

    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = ImportError("No module named 'boto3'")

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        out = service.list_backups()

    assert out == []


def test_list_backups_when_client_error_returns_empty():
    """boto3 presente ma il client solleva (403/404/etc) → [] (non solleva)."""
    from app.core.admin_backups import service

    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = RuntimeError("AccessDenied simulato")

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        out = service.list_backups()

    assert out == []


def test_list_backups_raises_on_missing_dependency():
    """Bug #4 (2026-04-23): MissingDependencyException (es. botocore[crt]
    assente per SSO provider) → AdminBackupDependencyError esplicito,
    NON lista vuota silenziosa. Il router traduce in HTTP 503."""
    from app.core.admin_backups import service
    from app.core.admin_backups.service import AdminBackupDependencyError
    from botocore.exceptions import MissingDependencyException

    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = MissingDependencyException(
        msg="Using the login credential provider requires an additional dependency."
    )

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        with pytest.raises(AdminBackupDependencyError) as exc_info:
            service.list_backups()

    assert "additional dependency" in str(exc_info.value)


def test_list_backups_raises_on_aws_credentials_expired():
    """RB.2 (2026-04-27): credenziali AWS scadute (LoginRefreshRequired)
    -> AdminBackupCredentialsError con messaggio operativo, NON lista
    vuota silenziosa. Il router traduce in HTTP 503 con
    detail.error='aws_credentials_expired'.

    current contract: list_backups passa per `get_backup_object_storage()` invece
    di `boto3.client` diretto, quindi mockiamo l'ObjectStorage."""
    from botocore.exceptions import LoginRefreshRequired

    from app.core.admin_backups import service
    from app.core.admin_backups.service import AdminBackupCredentialsError

    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = LoginRefreshRequired()

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        with pytest.raises(AdminBackupCredentialsError) as exc_info:
            service.list_backups()

    msg = str(exc_info.value)
    assert "AWS credentials expired" in msg
    assert "aws sso login" in msg


def test_list_backups_aws_credentials_logs_warning_without_traceback():
    """R-1 (2026-04-27): credenziali AWS scadute = condizione operativa nota
    (l'operatore deve fare `aws sso login`), gia' tradotta in 503 strutturato
    dal router. Il backend NON deve emettere traceback botocore completo per
    ogni 503: rumoreggia il journal e nasconde i bug veri.

    Previous behavior: `logger.error + exc_info=True` -> traceback ad ogni client call.
    Current contract: `logger.warning` conciso con tipo eccezione + messaggio.
    Errori NON classificati continuano a usare logger.error + exc_info=True
    (test successivo `test_list_backups_when_client_error_returns_empty`
    indirettamente).
    """
    from botocore.exceptions import LoginRefreshRequired

    from app.core.admin_backups import service
    from app.core.admin_backups.service import AdminBackupCredentialsError

    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = LoginRefreshRequired()

    spy_warning = MagicMock()
    spy_error = MagicMock()

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ), patch.object(
        service.logger, "warning", spy_warning
    ), patch.object(service.logger, "error", spy_error):
        with pytest.raises(AdminBackupCredentialsError):
            service.list_backups()

    # Esattamente 1 warning conciso, nessuna chiamata error+exc_info per
    # questo path (path classificato AWS-credentials).
    assert spy_warning.call_count == 1, (
        f"Atteso 1 warning conciso, trovati {spy_warning.call_count}"
    )
    warn_msg = spy_warning.call_args.args[0]
    assert "credenziali AWS" in warn_msg
    assert "LoginRefreshRequired" in warn_msg
    # logger.error NON deve essere chiamato (no traceback)
    assert spy_error.call_count == 0, (
        f"logger.error non deve girare per aws-creds: chiamate={spy_error.call_args_list}"
    )
    # Il warning concise NON deve essere chiamato con exc_info=True
    assert spy_warning.call_args.kwargs.get("exc_info") in (None, False)


def test_list_backups_raises_on_access_denied_refresh_token():
    """RB.2: AccessDeniedException con 'refresh token has expired' nel
    Message (caso reale dalla telemetria, BUG.md sezione 1) e' il modo
    in cui il provider SSO veicola la scadenza dietro un ClientError.
    Deve essere mappato come credentials expired, non come errore generico."""
    from botocore.exceptions import ClientError

    from app.core.admin_backups import service
    from app.core.admin_backups.service import AdminBackupCredentialsError

    error_response = {
        "Error": {
            "Code": "AccessDeniedException",
            "Message": "CreateOAuth2Token: The refresh token has expired",
        }
    }
    fake_storage = MagicMock()
    fake_storage.list_objects.side_effect = ClientError(
        error_response, "ListObjectsV2"
    )

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        with pytest.raises(AdminBackupCredentialsError):
            service.list_backups()


def test_list_backups_empty_bucket_returns_empty():
    from app.core.admin_backups import service

    fake_storage = MagicMock()
    fake_storage.list_objects.return_value = []

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        out = service.list_backups()

    assert out == []


def test_list_backups_sorts_newest_first():
    """list_backups chiama list_objects_v2 con Prefix='plenora_',
    mappa shape ObjectMeta, ordina per last_modified desc.
    """
    from app.core.admin_backups import service

    fake_storage = MagicMock()
    fake_storage.list_objects.return_value = [
        {
            "key": "plenora_20260301_020000Z.dump",
            "size": 100,
            "last_modified": datetime(2026, 3, 1, 2, 0, tzinfo=timezone.utc),
        },
        {
            "key": "plenora_20260418_020000Z.dump",
            "size": 200,
            "last_modified": datetime(2026, 4, 18, 2, 0, tzinfo=timezone.utc),
        },
    ]

    with patch(
        "app.infrastructure.storage.cloud_backends.get_backup_object_storage",
        return_value=fake_storage,
    ):
        out = service.list_backups()

    assert len(out) == 2
    assert out[0]["key"] == "plenora_20260418_020000Z.dump"  # newer first
    assert out[1]["key"] == "plenora_20260301_020000Z.dump"


# get_last_run_status — legge da plenora.backup_runs (DB-based)


def test_last_run_status_when_db_raises_returns_unknown():
    from app.core.admin_backups import service

    with patch(
        "app.db.pg_connection.fetchone",
        side_effect=ConnectionError("postgres unreachable"),
    ):
        out = service.get_last_run_status()

    assert out["result"] == "unknown"
    assert "postgres unreachable" in out["error_message"]
    assert out["exit_code"] is None
    assert out["log_tail"] == []


def test_last_run_status_when_no_runs_registered():
    from app.core.admin_backups import service

    with patch("app.db.pg_connection.fetchone", return_value=None):
        out = service.get_last_run_status()

    assert out["result"] == "unknown"
    assert out["started_at"] is None
    assert out["finished_at"] is None
    assert out["bucket_key"] is None
    assert out["log_tail"] == []


def test_last_run_status_success_with_duration():
    from app.core.admin_backups import service

    start = datetime(2026, 4, 18, 2, 0, 0, tzinfo=timezone.utc)
    finish = datetime(2026, 4, 18, 2, 3, 30, tzinfo=timezone.utc)
    row = {
        "id": 42,
        "started_at": start,
        "finished_at": finish,
        "exit_code": 0,
        "result": "success",
        "bucket_key": "urby_20260418_020000Z.dump",
        "size_bytes": 23 * 1024 * 1024,
        "log_tail": "[2026-04-18T02:00:00Z] [INFO] backup start\n"
                    "[2026-04-18T02:03:30Z] [INFO] backup completato con successo",
        "error_message": None,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_last_run_status()

    assert out["result"] == "success"
    assert out["exit_code"] == 0
    assert out["bucket_key"] == "urby_20260418_020000Z.dump"
    assert out["size_bytes"] == 23 * 1024 * 1024
    assert out["duration_sec"] == 210.0
    assert out["started_at"].startswith("2026-04-18T02:00:00")
    assert out["finished_at"].startswith("2026-04-18T02:03:30")
    assert len(out["log_tail"]) == 2


def test_last_run_status_failed_with_error():
    from app.core.admin_backups import service

    start = datetime(2026, 4, 18, 2, 0, 0, tzinfo=timezone.utc)
    finish = datetime(2026, 4, 18, 2, 0, 5, tzinfo=timezone.utc)
    row = {
        "id": 43,
        "started_at": start,
        "finished_at": finish,
        "exit_code": 1,
        "result": "failed",
        "bucket_key": None,
        "size_bytes": None,
        "log_tail": "[2026-04-18T02:00:05Z] [ERROR] pg_dump fallito",
        "error_message": "pg_dump fallito (exit 1)",
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_last_run_status()

    assert out["result"] == "failed"
    assert out["exit_code"] == 1
    assert out["bucket_key"] is None
    assert out["error_message"] == "pg_dump fallito (exit 1)"


def test_last_run_status_running_has_no_duration():
    """Job in corso: started_at presente, finished_at None, duration None."""
    from app.core.admin_backups import service

    row = {
        "id": 44,
        "started_at": datetime.now(timezone.utc),
        "finished_at": None,
        "exit_code": None,
        "result": "running",
        "bucket_key": None,
        "size_bytes": None,
        "log_tail": None,
        "error_message": None,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_last_run_status()

    assert out["result"] == "running"
    assert out["finished_at"] is None
    assert out["duration_sec"] is None


def test_last_run_status_duration_with_iso_string_timestamps():
    """Bug runtime-bugs #3 (2026-04-27): il driver PG puo' restituire
    started_at/finished_at come stringhe ISO TZ-aware invece che datetime.
    Previous behavior: TypeError sulla sottrazione str-str -> WARNING + duration_sec=None.
    Current contract: normalizzazione via _to_datetime -> duration_sec calcolato.

    Telemetria osservata:
        Impossibile calcolare duration_sec
        (started='2026-04-27 02:02:45.362688+00:00',
         finished='2026-04-27 02:02:50.417383+00:00'):
        TypeError: unsupported operand type(s) for -: 'str' and 'str'
    """
    from app.core.admin_backups import service

    started_iso = "2026-04-27 02:02:45.362688+00:00"
    finished_iso = "2026-04-27 02:02:50.417383+00:00"
    row = {
        "id": 99,
        "started_at": started_iso,
        "finished_at": finished_iso,
        "exit_code": 0,
        "result": "success",
        "bucket_key": "plenora_20260427_020245Z.dump",
        "size_bytes": 1024,
        "log_tail": None,
        "error_message": None,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_last_run_status()

    assert out["result"] == "success"
    # Atteso: 5.054695s (50.417383 - 45.362688). Tolleranza floating-point.
    assert out["duration_sec"] is not None, (
        "duration_sec deve essere calcolato anche con timestamp stringa ISO"
    )
    assert abs(out["duration_sec"] - 5.054695) < 1e-3
    # Stringhe ISO conservate via str() fallback (no .isoformat() su str)
    assert out["started_at"] == started_iso
    assert out["finished_at"] == finished_iso


def test_last_run_status_duration_unparsable_iso_string_logs_warning():
    """Difesa: stringa non-ISO (es. driver custom corrotto) NON deve far
    crashare get_last_run_status. Cade nell'except (ValueError) -> warning
    + duration_sec=None, coerente con il Previous behavior per altri tipi inattesi."""
    from app.core.admin_backups import service

    row = {
        "id": 100,
        "started_at": "non-una-data-iso",
        "finished_at": "neanche-questa",
        "exit_code": 0,
        "result": "success",
        "bucket_key": None,
        "size_bytes": None,
        "log_tail": None,
        "error_message": None,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_last_run_status()

    # No crash, no traceback all'utente
    assert out["result"] == "success"
    assert out["duration_sec"] is None


# get_schedule_info — metadati statici (container-friendly, no systemctl)


def test_schedule_info_returns_default_cron(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.delenv("BACKUP_SCHEDULE_CRON", raising=False)
    monkeypatch.delenv("BACKUP_SCHEDULE_DESCRIPTION", raising=False)

    out = service.get_schedule_info()

    assert out["schedule_cron"] == "0 2 * * *"
    assert out["timezone"] == "UTC"
    assert "02:00" in out["description"]


def test_schedule_info_respects_env_override(monkeypatch):
    from app.core.admin_backups import service

    monkeypatch.setenv("BACKUP_SCHEDULE_CRON", "0 4 * * *")
    monkeypatch.setenv("BACKUP_SCHEDULE_DESCRIPTION", "Daily 04:00 UTC")

    out = service.get_schedule_info()

    assert out["schedule_cron"] == "0 4 * * *"
    assert out["description"] == "Daily 04:00 UTC"
