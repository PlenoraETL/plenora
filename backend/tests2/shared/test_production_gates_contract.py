"""Contract tests for production/staging and cluster startup gates.

Plenora 2.0 Fase 5: il broker e' RabbitMQ (Dramatiq). Il gate loopback
controlla ``DRAMATIQ_BROKER_URL`` (era CELERY_BROKER_URL/RESULT_BACKEND/
REDIS_URL). I flag hanno i nomi nuovi ``*_DISTRIBUTED_*``; i nomi storici
``*_REDIS_*`` restano accettati come alias env deprecati
(``Settings._DEPRECATED_ENV_ALIASES``) — coperto dal test alias sotto.
"""

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
        "DRAMATIQ_BROKER_URL",
        "OBJECT_STORAGE_ENABLED",
        "OBJECT_STORAGE_BUCKET",
        "SCHEDULE_BEAT_LEADER_FAIL_OPEN",
        "LOGIN_RL_DISTRIBUTED_REQUIRED",
        "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED",
        "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED",
        "STORAGE_POLICY",
        # Alias legacy: un valore residuo in env inquinerebbe i gate
        # attraverso Settings._DEPRECATED_ENV_ALIASES.
        "LOGIN_RL_REDIS_REQUIRED",
        "ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED",
        "ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED",
        "PLENORA_SINGLE_NODE",
    ):
        monkeypatch.delenv(name, raising=False)


def _errors_for_current_env() -> list[str]:
    errors: list[str] = []
    startup_validation.validate_production_gates(Settings(), errors)
    return errors


def _set_cluster_safe_env(monkeypatch, *, environment: str = "production") -> None:
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.setenv(
        "DRAMATIQ_BROKER_URL", "amqp://guest:guest@rabbitmq.service:5672"
    )
    monkeypatch.setenv("OBJECT_STORAGE_ENABLED", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-artifacts")
    monkeypatch.setenv("SCHEDULE_BEAT_LEADER_FAIL_OPEN", "false")
    monkeypatch.setenv("LOGIN_RL_DISTRIBUTED_REQUIRED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED", "true")
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")


def test_dev_defaults_do_not_enable_production_gates():
    errors = _errors_for_current_env()

    assert errors == []


def test_legacy_alias_env_still_satisfies_gates(monkeypatch):
    """Un deploy con i nomi env storici *_REDIS_* nel .env deve passare
    i gate: Settings risolve gli alias deprecati in modo trasparente."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "DRAMATIQ_BROKER_URL", "amqp://guest:guest@rabbitmq.service:5672"
    )
    monkeypatch.setenv("OBJECT_STORAGE_ENABLED", "true")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "runtime-artifacts")
    monkeypatch.setenv("SCHEDULE_BEAT_LEADER_FAIL_OPEN", "false")
    monkeypatch.setenv("STORAGE_POLICY", "hybrid")
    # SOLO i nomi legacy: i nuovi restano unset.
    monkeypatch.setenv("LOGIN_RL_REDIS_REQUIRED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_ENABLED", "true")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_REDIS_REQUIRED", "true")

    assert _errors_for_current_env() == []


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "stage"])
def test_server_envs_enable_production_gates(monkeypatch, environment):
    monkeypatch.setenv("ENVIRONMENT", environment)

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "OBJECT_STORAGE_BUCKET" in joined
    assert "LOGIN_RL_DISTRIBUTED_REQUIRED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED" in joined


@pytest.mark.parametrize(
    "flag_name",
    ["CLUSTER_MODE", "PLENORA_CLUSTER_MODE", "MULTI_POD_MODE", "PLENORA_MULTI_POD"],
)
def test_explicit_cluster_flags_enable_gates_in_development(monkeypatch, flag_name):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv(flag_name, "true")

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "OBJECT_STORAGE_BUCKET" in joined


@pytest.mark.parametrize(
    "url",
    [
        "amqp://guest:guest@localhost:5672",
        "amqp://guest:guest@127.0.0.1:5672",
        "amqp://guest:guest@[::1]:5672",
        "amqp://guest:guest@0.0.0.0:5672",
    ],
)
def test_loopback_broker_urls_are_rejected(monkeypatch, url):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("DRAMATIQ_BROKER_URL", url)

    errors = _errors_for_current_env()

    assert any(
        "DRAMATIQ_BROKER_URL" in error and "loopback/localhost" in error
        for error in errors
    )


def test_single_node_declared_allows_loopback_broker(monkeypatch):
    """PLENORA_SINGLE_NODE=true (installer nativo Windows, single-box
    dichiarato): il gate anti-loopback sul broker viene soppresso —
    RabbitMQ sulla stessa macchina e' la topologia del prodotto."""
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("DRAMATIQ_BROKER_URL", "amqp://plenora:pw@127.0.0.1:5672")
    monkeypatch.setenv("PLENORA_SINGLE_NODE", "true")

    assert _errors_for_current_env() == []


def test_single_node_does_not_disable_other_gates(monkeypatch):
    """Il flag single-node sopprime SOLO il check loopback: gli altri
    gates (object storage, rate-limiter distribuiti) restano attivi."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PLENORA_SINGLE_NODE", "true")
    monkeypatch.setenv("DRAMATIQ_BROKER_URL", "amqp://plenora:pw@127.0.0.1:5672")

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "loopback" not in joined
    assert "OBJECT_STORAGE_BUCKET" in joined
    assert "LOGIN_RL_DISTRIBUTED_REQUIRED" in joined


def test_loopback_broker_url_error_does_not_expose_credentials(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv(
        "DRAMATIQ_BROKER_URL",
        "amqp://guest:super-secret-password@localhost:5672",
    )

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "DRAMATIQ_BROKER_URL" in joined
    assert "localhost:5672" in joined
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
    monkeypatch.setenv("LOGIN_RL_DISTRIBUTED_REQUIRED", "false")

    errors = _errors_for_current_env()

    assert any("LOGIN_RL_DISTRIBUTED_REQUIRED" in error for error in errors)


def test_arricchisci_rate_limiter_must_use_required_redis(monkeypatch):
    _set_cluster_safe_env(monkeypatch)
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED", "false")
    monkeypatch.setenv("ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED", "false")

    errors = _errors_for_current_env()
    joined = "\n".join(errors)

    assert "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_ENABLED" in joined
    assert "ARRICCHISCI_RATE_LIMIT_DISTRIBUTED_REQUIRED" in joined


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
    monkeypatch.setattr(startup_validation, "ensure_broker", lambda *_a: None)

    with pytest.raises(SystemExit) as exc_info:
        startup_validation.validate_startup(Settings())

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "Configurazione non valida" in out
    assert "OBJECT_STORAGE_BUCKET" in out
