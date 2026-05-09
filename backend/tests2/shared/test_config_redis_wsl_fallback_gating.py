"""
Contract test BE-GO-READY-INSTALLATION-003-REDIS-WSL-FALLBACK-GATING.

Coperti:
"""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from app.config import Settings
from app.startup_validation import ensure_redis, wsl_redis_fallback_allowed


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Helpers


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_redis_reachable(monkeypatch, *, reachable: bool) -> None:
    def _create_connection(*args, **kwargs):
        if reachable:
            return _FakeSocket()
        raise OSError("redis unreachable (simulated)")

    monkeypatch.setattr(socket, "create_connection", _create_connection)


def _record_popen(monkeypatch) -> list:
    """Cattura le chiamate a `subprocess.Popen`; previene side effect.

    Anche se il test gira su una macchina dove `wsl` esiste davvero,
    il sostituto evita di lanciare il binario reale.
    """
    calls: list = []

    def _fake_popen(args, *_a, **_kw):
        calls.append(list(args) if isinstance(args, (list, tuple)) else args)
        raise FileNotFoundError("simulated: wsl not invoked in tests")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)
    return calls


def _patch_platform_env(
    monkeypatch, *, platform: str, environment: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv("ENVIRONMENT", environment)
    monkeypatch.delenv("ENV", raising=False)


# Gating predicate


@pytest.mark.parametrize(
    "platform, environment, expected",
    [
        # Dev + Windows -> fallback ammesso
        ("win32", "development", True),
        ("win32", "dev", True),
        # Server envs su Windows -> fallback NON ammesso
        ("win32", "production", False),
        ("win32", "prod", False),
        ("win32", "staging", False),
        ("win32", "stage", False),
        # Linux dev -> fallback NON ammesso (WSL e' un dettaglio Windows)
        ("linux", "development", False),
        ("linux", "dev", False),
        # Linux server -> fallback NON ammesso
        ("linux", "production", False),
        ("linux", "prod", False),
        # Darwin trattato come server (non Windows)
        ("darwin", "development", False),
    ],
)
def test_wsl_fallback_gating(monkeypatch, platform, environment, expected):
    _patch_platform_env(monkeypatch, platform=platform, environment=environment)
    assert wsl_redis_fallback_allowed() is expected


def test_wsl_fallback_gating_env_fallback_to_env_var(monkeypatch):
    """Se ENVIRONMENT non e' settato, il gating consulta `ENV`."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setenv("ENV", "production")
    assert wsl_redis_fallback_allowed() is False


def test_wsl_fallback_gating_default_dev(monkeypatch):
    """Default (`ENVIRONMENT`/`ENV` assenti) = development."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.delenv("ENV", raising=False)
    assert wsl_redis_fallback_allowed() is True


# ensure_redis: server path (no WSL, operational message)


@pytest.mark.parametrize(
    "platform, environment",
    [
        ("linux", "development"),  # Linux dev: WSL non disponibile
        ("linux", "production"),   # Linux prod: server canonico
        ("linux", "staging"),
        ("win32", "production"),   # Windows server hardened
        ("win32", "staging"),
        ("darwin", "development"),
    ],
)
def test_server_path_no_wsl_attempt(monkeypatch, platform, environment):
    _patch_platform_env(monkeypatch, platform=platform, environment=environment)
    _patch_redis_reachable(monkeypatch, reachable=False)
    popen_calls = _record_popen(monkeypatch)
    monkeypatch.setenv(
        "CELERY_BROKER_URL", "redis://my-redis-cluster.example.com:6379/0"
    )

    settings = Settings()
    errors: list = []
    warnings: list = []
    ensure_redis(settings, errors, warnings)

    assert popen_calls == [], (
        f"server path deve evitare subprocess.Popen WSL; chiamate: {popen_calls}"
    )
    assert len(errors) == 1, f"atteso 1 errore, ricevuti: {errors}"
    msg = errors[0]
    assert "redis://my-redis-cluster.example.com:6379/0" in msg, (
        f"il messaggio deve citare il broker URL configurato: {msg!r}"
    )
    assert "wsl" not in msg.lower(), (
        f"il messaggio server NON deve menzionare WSL: {msg!r}"
    )
    assert warnings == []


def test_server_path_uses_default_url_when_unset(monkeypatch):
    _patch_platform_env(monkeypatch, platform="linux", environment="production")
    _patch_redis_reachable(monkeypatch, reachable=False)
    _record_popen(monkeypatch)
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)

    settings = Settings()
    errors: list = []
    warnings: list = []
    ensure_redis(settings, errors, warnings)

    assert len(errors) == 1
    assert "redis://localhost:6379/0" in errors[0]
    assert "wsl" not in errors[0].lower()


# ensure_redis: dev Windows path (WSL fallback preserved)


def test_dev_windows_path_invokes_wsl(monkeypatch):
    _patch_platform_env(monkeypatch, platform="win32", environment="development")
    _patch_redis_reachable(monkeypatch, reachable=False)
    popen_calls = _record_popen(monkeypatch)

    settings = Settings()
    errors: list = []
    warnings: list = []
    ensure_redis(settings, errors, warnings)

    # Esattamente un tentativo WSL: il fake Popen solleva
    # FileNotFoundError, gestito come errore "WSL non disponibile".
    assert len(popen_calls) == 1
    assert popen_calls[0][0] == "wsl"
    assert popen_calls[0][1] == "redis-server"
    # Sul path dev Windows il messaggio puo' citare WSL (e' una guida
    # per il dev, non per l'operatore di un server).
    assert any("wsl" in e.lower() for e in errors)


# ensure_redis: Redis raggiungibile -> nessun side effect


@pytest.mark.parametrize(
    "platform, environment",
    [
        ("win32", "development"),
        ("linux", "production"),
    ],
)
def test_redis_reachable_no_side_effects(monkeypatch, platform, environment):
    _patch_platform_env(monkeypatch, platform=platform, environment=environment)
    _patch_redis_reachable(monkeypatch, reachable=True)
    popen_calls = _record_popen(monkeypatch)

    settings = Settings()
    errors: list = []
    warnings: list = []
    ensure_redis(settings, errors, warnings)

    assert errors == []
    assert warnings == []
    assert popen_calls == []
