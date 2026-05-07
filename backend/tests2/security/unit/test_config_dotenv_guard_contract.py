"""
Tests the config dotenv guard behavior.

Locks backend contracts and regression boundaries for the current architecture.
"""

import logging

import pytest

from app.config import _should_load_dotenv


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)


def test_default_environment_loads_dotenv():
    assert _should_load_dotenv() is True


def test_explicit_development_loads_dotenv(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    assert _should_load_dotenv() is True


@pytest.mark.parametrize("env_value", ["production", "prod", "staging", "stage"])
def test_prod_and_staging_skip_dotenv(monkeypatch, env_value):
    monkeypatch.setenv("ENVIRONMENT", env_value)

    assert _should_load_dotenv() is False


@pytest.mark.parametrize("env_value", ["Production", "PROD", "Staging", "STAGE"])
def test_prod_and_staging_are_case_insensitive(monkeypatch, env_value):
    monkeypatch.setenv("ENVIRONMENT", env_value)

    assert _should_load_dotenv() is False


def test_env_fallback_is_honored_when_environment_is_unset(monkeypatch):
    monkeypatch.setenv("ENV", "production")

    assert _should_load_dotenv() is False


def test_environment_wins_over_env_when_both_set(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENV", "production")

    assert _should_load_dotenv() is True


def test_unknown_environment_loads_dotenv_fail_open_on_dev_like_names():
    # Un nome non nella deny-list è trattato come dev-like: carica il .env.
    # Questo è intenzionale — la deny-list è la fonte di verità, non la allow-list.
    import os
    os.environ["ENVIRONMENT"] = "canary"
    try:
        assert _should_load_dotenv() is True
    finally:
        del os.environ["ENVIRONMENT"]


def test_config_module_does_not_crash_when_loaded_in_production(monkeypatch, caplog):
    # In produzione, anche se backend/.env esiste, il modulo deve caricarsi
    # senza crash. Il warning è loggato ma non è un errore.
    monkeypatch.setenv("ENVIRONMENT", "production")
    # Ricarico il modulo per far rieseguire il blocco dotenv con il nuovo env.
    import importlib

    import app.config

    with caplog.at_level(logging.WARNING, logger="app.config"):
        importlib.reload(app.config)

    # Non crasha. Il warning potrebbe non essere catturato se .env non esiste
    # sull'host corrente — non lo asseriamo, vogliamo solo la robustezza.
    assert app.config.Settings is not None
