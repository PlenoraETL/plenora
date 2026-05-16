"""Contract tests for production/staging and cluster startup gates."""

from __future__ import annotations

import pytest

from app.config import Settings
import app.startup_validation as startup_validation


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clean_gate_env(monkeypatch):
    for name in (
        "ENVIRONMENT",
        "ENV",
        "CLUSTER_MODE",
        "PLENORA_CLUSTER_MODE",
        "MULTI_POD_MODE",
        "PLENORA_MULTI_POD",
        "CELERY_BROKER_URL",
        "CELERY_RESULT_BACKEND",
        "REDIS_URL",
        "OBJECT_STORAGE_ENABLED",
        "OBJECT_STORAGE_BUCKET",
        "SCHEDULE_BEAT_LEADER_FAIL_OPEN",
        "LOGIN_RL_REDIS_REQUIRED",
        "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED",
        "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED",
        "ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED",
        "STORAGE_POLICY",
    ):
        monkeypatch.delenv(name, raising=False)


def _errors_for_current_env() -> list[str]:
    errors: list[str] = []
    startup_validation.validate_production_gates(Settings(), errors)
    return errors


def _set_cluster_safe_env(monkeypatch, *, environment: str = "production") -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://redis.service:6379/0")
    monkeypatch.setenv("CELERY_RESULT_BACKEND", "redis://redis.service:6379/1")
    monkeypatch.setenv("OBJECT_STORAGE_ENABLED", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-artifacts")
    monkeypatch.setenv("SCHEDULE_BEAT_LEADER_FAIL_OPEN", "false")
    monkeypatch.setenv("LOGIN_RL_REDIS_REQUIRED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", "true")
    monkeypatch.setenv("ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED", "false")
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")


def test_dev_defaults_do_not_enable_production_gates():
    errors = _errors_for_current_env()

    assert errors == []


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "stage"])
def test_server_envs_enable_production_gates(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "CELERY_BROKER_URL" in joined
    assert "CELERY_RESULT_BACKEND" in joined
    assert "OBJECT_STORAGE_BUCKET" in joined
    assert "LOGIN_RL_REDIS_REQUIRED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED" in joined
    assert "ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED" in joined


@pytest.mark.parametrize(
    "flag_name",
    ["CLUSTER_MODE", "PLENORA_CLUSTER_MODE", "MULTI_POD_MODE", "PLENORA_MULTI_POD"],
)
def test_explicit_cluster_flags_enable_gates_in_development(monkeypatch, flag_name):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(flag_name, "true")

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "CELERY_BROKER_URL" in joined
    assert "OBJECT_STORAGE_BUCKET" in joined


@pytest.mark.parametrize(
    "key,url",
    [
        ("CELERY_BROKER_URL", "redis://localhost:6379/0"),
        ("CELERY_BROKER_URL", "redis://127.0.0.1:6379/0"),
        ("CELERY_RESULT_BACKEND", "redis://[::1]:6379/1"),
        ("REDIS_URL", "redis://0.0.0.0:6379/0"),
    ],
)
def test_loopback_redis_urls_are_rejected(monkeypatch, key, url):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv(key, url)

    errors = _errors_for_current_env()

    assert any(key in error and "loopback/localhost" in error for error in errors)


def test_loopback_redis_url_error_does_not_expose_credentials(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv(
        "CELERY_BROKER_URL",
        "redis://:super-secret-password@localhost:6379/0",
    )

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "CELERY_BROKER_URL" in joined
    assert "localhost:6379" in joined
    assert "super-secret-password" not in joined


def test_remote_cluster_safe_config_passes(monkeypatch):
    _set_cluster_safe_env(monkeypatch, environment="staging")

    assert _errors_for_current_env() == []


def test_object_storage_capability_must_be_enabled(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("OBJECT_STORAGE_ENABLED", "false")

    errors = _errors_for_current_env()

    assert any("OBJECT_STORAGE_ENABLED" in error for error in errors)


def test_beat_leader_fail_open_is_rejected(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("SCHEDULE_BEAT_LEADER_FAIL_OPEN", "true")

    errors = _errors_for_current_env()

    assert any("SCHEDULE_BEAT_LEADER_FAIL_OPEN" in error for error in errors)


def test_login_rate_limiter_must_require_redis(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("LOGIN_RL_REDIS_REQUIRED", "false")

    errors = _errors_for_current_env()

    assert any("LOGIN_RL_REDIS_REQUIRED" in error for error in errors)


def test_arricchisci_rate_limiter_must_use_required_redis(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", "false")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", "false")

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED" in joined


def test_admin_backup_local_trigger_must_be_disabled(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED", "true")

    errors = _errors_for_current_env()

    assert any("ADMIN_BACKUP_LOCAL_TRIGGER_ENABLED" in error for error in errors)


def test_storage_policy_db_is_rejected_for_cluster(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("STORAGE_POLICY", "db")

    errors = _errors_for_current_env()

    assert any("STORAGE_POLICY='db'" in error for error in errors)


def test_validate_startup_runs_production_gates_before_boot_completes(
    monkeypatch, capsys,
):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(startup_validation, "_validate_data_dir", lambda *_a: None)
    monkeypatch.setattr(startup_validation, "_cleanup_temp_dir", lambda *_a: None)
    monkeypatch.setattr(startup_validation, "_cleanup_legacy_dirs", lambda *_a: None)
    monkeypatch.setattr(startup_validation, "ensure_redis", lambda *_a: None)

    with pytest.raises(SystemExit) as exc_info:
        startup_validation.validate_startup(Settings())

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Configurazione non valida" in out
    assert "CELERY_BROKER_URL" in out
    assert "OBJECT_STORAGE_BUCKET" in out
