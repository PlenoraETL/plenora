"""
Contract test: startup broker (RabbitMQ) reachability probe.

Plenora 2.0 Fase 5: rimpiazza il vecchio
BE-GO-READY-INSTALLATION-003-REDIS-WSL-FALLBACK-GATING. Il broker dei task
e' RabbitMQ (Dramatiq), non piu' Redis (Celery): ``ensure_broker`` fa un
connect TCP bounded all'host:porta di ``DRAMATIQ_BROKER_URL`` e fail-fast se
il broker e' irraggiungibile. Il fallback WSL per auto-avviare Redis in dev
Windows e' stato rimosso col cutover (non applicabile a RabbitMQ).
"""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from app.config import Settings
from app.startup_validation import ensure_broker


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _FakeSocket:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _patch_broker_reachable(monkeypatch, *, reachable: bool) -> None:
    def _create_connection(*args, **kwargs):
        if reachable:
            return _FakeSocket()
        raise OSError("broker unreachable (simulated)")

    monkeypatch.setattr(socket, "create_connection", _create_connection)


def _forbid_subprocess(monkeypatch) -> None:
    """``ensure_broker`` non deve MAI avviare processi figli (a differenza
    del vecchio fallback WSL Redis)."""

    def _fake_popen(*_a, **_kw):
        raise AssertionError("ensure_broker non deve spawnare processi")

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)


# Broker irraggiungibile -> un solo errore, cita l'URL del broker


def test_broker_unreachable_appends_single_error(monkeypatch):
    _patch_broker_reachable(monkeypatch, reachable=False)
    _forbid_subprocess(monkeypatch)
    monkeypatch.setenv(
        "DRAMATIQ_BROKER_URL", "amqp://guest:guest@broker.example.com:5672"
    )

    errors: list = []
    ensure_broker(Settings(), errors)

    assert len(errors) == 1, f"atteso 1 errore, ricevuti: {errors}"
    msg = errors[0]
    assert "amqp://guest:guest@broker.example.com:5672" in msg, (
        f"il messaggio deve citare il broker URL configurato: {msg!r}"
    )
    assert "RabbitMQ" in msg
    assert "redis" not in msg.lower(), (
        f"il messaggio non deve menzionare Redis: {msg!r}"
    )


def test_broker_unreachable_uses_default_url_when_unset(monkeypatch):
    _patch_broker_reachable(monkeypatch, reachable=False)
    _forbid_subprocess(monkeypatch)
    monkeypatch.delenv("DRAMATIQ_BROKER_URL", raising=False)

    errors: list = []
    ensure_broker(Settings(), errors)

    assert len(errors) == 1
    assert "amqp://guest:guest@rabbitmq:5672" in errors[0]


# Broker raggiungibile -> nessun errore, nessun side effect, su ogni OS


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
def test_broker_reachable_no_error_no_side_effects(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    _patch_broker_reachable(monkeypatch, reachable=True)
    _forbid_subprocess(monkeypatch)

    errors: list = []
    ensure_broker(Settings(), errors)

    assert errors == []
