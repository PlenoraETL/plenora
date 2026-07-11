"""Contract del meccanismo di alias env deprecati in Settings.

Plenora 2.0: le chiavi config storiche Celery/Redis sono state rinominate
(TASK_*, *_DISTRIBUTED_*) ma i deploy esistenti possono avere ancora i
nomi vecchi nel .env. `Settings.get` risolve: nome nuovo in env → alias
legacy in env (warning una-tantum) → DEFAULTS.
"""

from __future__ import annotations

import pytest

from app.config import Settings


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_ALIAS_KEYS = sorted(Settings._DEPRECATED_ENV_ALIASES.items())


@pytest.fixture(autouse=True)
def _clean_alias_env(monkeypatch):
    for new, legacy in _ALIAS_KEYS:
        monkeypatch.delenv(new, raising=False)
        monkeypatch.delenv(legacy, raising=False)


def test_alias_map_targets_exist_in_defaults():
    """Ogni nome nuovo della mappa alias deve esistere in DEFAULTS (un
    typo nella mappa passerebbe altrimenti inosservato) e nessun nome
    legacy deve essere rimasto in DEFAULTS."""
    for new, legacy in _ALIAS_KEYS:
        assert new in Settings.DEFAULTS, f"alias target {new} non in DEFAULTS"
        assert legacy not in Settings.DEFAULTS, (
            f"chiave legacy {legacy} ancora in DEFAULTS: il rename e' a meta'"
        )


def test_legacy_env_is_honored_when_new_name_unset(monkeypatch):
    monkeypatch.setenv("CELERY_TASK_HARD_LIMIT", "1234")

    assert Settings().get("TASK_HARD_LIMIT") == 1234


def test_new_name_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("CELERY_TASK_HARD_LIMIT", "1234")
    monkeypatch.setenv("TASK_HARD_LIMIT", "42")

    assert Settings().get("TASK_HARD_LIMIT") == 42


def test_defaults_apply_when_neither_is_set():
    assert Settings().get("TASK_HARD_LIMIT") == 3900


def test_legacy_bool_coercion_uses_new_key_type(monkeypatch):
    """La coercizione di tipo del valore legacy segue il default del
    nome NUOVO (bool), non una stringa raw."""
    monkeypatch.setenv("LOGIN_RL_REDIS_REQUIRED", "true")

    assert Settings().get("LOGIN_RL_DISTRIBUTED_REQUIRED") is True


def test_legacy_env_logs_deprecation_warning_once(monkeypatch):
    monkeypatch.setenv("CELERY_QUEUE_DEFAULT", "coda-legacy")
    # Reset dello stato warn-once condiviso a livello di classe.
    monkeypatch.setattr(Settings, "_alias_warned", set())
    warnings: list[str] = []

    def _capture_warning(message, *args):
        warnings.append(message % args)

    monkeypatch.setattr("app.config.logger.warning", _capture_warning)

    settings = Settings()
    assert settings.get("TASK_QUEUE_DEFAULT") == "coda-legacy"
    assert settings.get("TASK_QUEUE_DEFAULT") == "coda-legacy"

    assert len(warnings) == 1, "il warning di deprecazione deve essere una-tantum"
    assert "CELERY_QUEUE_DEFAULT" in warnings[0]
    assert "TASK_QUEUE_DEFAULT" in warnings[0]
