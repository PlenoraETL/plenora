"""Contract dell'harness env dei test funzionali/integration.

Plenora 2.0: l'isolamento dei worker di test su RabbitMQ e' via code
suffissate (``isolate_task_queues``), non piu' via DB Redis separati.
Questi contratti girano nella lane di default (nessun broker richiesto:
gli helper manipolano solo dict di env).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.functional.harness import env as harness_env


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# 1. configure_broker_env


def test_broker_env_is_env_first():
    env = {"DRAMATIQ_BROKER_URL": "amqp://guest:guest@rabbitmq:5672"}
    harness_env.configure_broker_env(env)
    assert env["DRAMATIQ_BROKER_URL"] == "amqp://guest:guest@rabbitmq:5672"


def test_broker_env_host_run_fallback(monkeypatch):
    monkeypatch.setattr(harness_env, "_running_in_container", lambda: False)
    env: dict = {}
    harness_env.configure_broker_env(env)
    assert env["DRAMATIQ_BROKER_URL"] == "amqp://guest:guest@127.0.0.1:5672"


def test_broker_env_fails_fast_in_container_without_url(monkeypatch):
    """In container il fallback 127.0.0.1 e' sempre sbagliato (il broker
    e' un service hostname): meglio errore esplicito subito."""
    monkeypatch.setattr(harness_env, "_running_in_container", lambda: True)
    with pytest.raises(RuntimeError, match="DRAMATIQ_BROKER_URL"):
        harness_env.configure_broker_env({})


# 2. isolate_task_queues


def test_isolate_task_queues_suffixes_all_four_queues():
    env: dict = {}
    suffix = harness_env.isolate_task_queues(env, suffix="abc123")
    assert suffix == "abc123"
    assert env == {
        "TASK_QUEUE_DEFAULT": "default-tabc123",
        "TASK_QUEUE_DATA_PLANE": "data-plane-tabc123",
        "TASK_QUEUE_CONTROL_PLANE": "control-plane-tabc123",
        "TASK_QUEUE_SCHEDULER": "scheduler-tabc123",
    }


def test_isolate_task_queues_random_suffix_diverges_from_defaults():
    """Senza suffisso esplicito ogni chiamata dedica code nuove: nessuna
    coda puo' coincidere coi nomi default consumati da un worker compose."""
    env_a: dict = {}
    env_b: dict = {}
    sfx_a = harness_env.isolate_task_queues(env_a)
    sfx_b = harness_env.isolate_task_queues(env_b)
    assert sfx_a != sfx_b
    defaults = set(harness_env._TASK_QUEUE_BASES.values())
    assert not (set(env_a.values()) & defaults)
    assert not (set(env_a.values()) & set(env_b.values()))


def test_queue_keys_match_settings_defaults():
    """Le chiavi suffissate devono essere ESATTAMENTE quelle che il
    routing Dramatiq legge da Settings: un typo qui renderebbe
    l'isolamento un no-op silenzioso (il worker resterebbe sulle code
    default)."""
    from app.config import Settings

    for key, base in harness_env._TASK_QUEUE_BASES.items():
        assert key in Settings.DEFAULTS, key
        assert Settings.DEFAULTS[key] == base, key


# 3. Zero-redis lock sull'harness


def test_functional_harness_has_no_redis_dependency():
    """Il pacchetto harness non deve importare redis (dev-dep rimossa):
    un import residuo esploderebbe solo a runtime sullo stack reale."""
    harness_dir = Path(harness_env.__file__).resolve().parent
    for p in sorted(harness_dir.glob("*.py")):
        source = p.read_text(encoding="utf-8").lower()
        assert "import redis" not in source, p.name
        assert "celery_broker_url" not in source, p.name
