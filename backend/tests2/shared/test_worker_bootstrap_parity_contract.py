"""
Contract: bootstrap del processo worker su middleware Dramatiq.

Plenora 2.0 Fase 5 (fetta 7): il bootstrap 4-step del worker non e' piu' su
signal Celery (worker_init / worker_process_init), ma sul middleware
``_BootstrapMiddleware.before_worker_boot`` del broker Dramatiq. Questo
contratto blinda la parita' del boundary:
  - ``before_worker_boot`` scarta il pool psycopg ereditato e ri-bootstrappa
    con ``force_reset=True`` (i worker Dramatiq nascono da fork del
    supervisor, come i child prefork di Celery);
  - ``_bootstrap_worker_process()`` di default e' idempotente;
  - una qualsiasi eccezione da ``bootstrap`` propaga (fail-closed).
"""

from __future__ import annotations

import os

# DATABASE_URL deve esistere prima di importare app.db.pg_connection
# (RuntimeError a module-level se mancante).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

from typing import List

import pytest

pytest.importorskip("dramatiq", reason="dramatiq non installato")

import app.bootstrap as bootstrap_module
import app.db.pg_connection as pg_connection
import workers.dramatiq_app as dramatiq_app


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture
def _reset_boot_flag():
    """Snapshot + restore di ``_BOOT_COMPLETE`` attorno al test."""
    original = bootstrap_module._BOOT_COMPLETE
    bootstrap_module._BOOT_COMPLETE = False
    try:
        yield
    finally:
        bootstrap_module._BOOT_COMPLETE = original


def _stub_bootstrap(monkeypatch) -> List[bool]:
    """Sostituisce ``app.bootstrap.bootstrap`` con un recorder che, come la
    vera implementazione, flippa ``_BOOT_COMPLETE`` a True. Ritorna la lista
    dei valori del flag visti all'ENTRATA di ogni invocazione."""
    calls_seen_flag: List[bool] = []

    def _fake_bootstrap():
        calls_seen_flag.append(bootstrap_module._BOOT_COMPLETE)
        bootstrap_module._BOOT_COMPLETE = True

    monkeypatch.setattr(bootstrap_module, "bootstrap", _fake_bootstrap)
    return calls_seen_flag


# _bootstrap_worker_process: API diretta


def test_bootstrap_worker_process_default_is_idempotent(
    monkeypatch, _reset_boot_flag,
):
    calls = _stub_bootstrap(monkeypatch)
    dramatiq_app._bootstrap_worker_process()
    dramatiq_app._bootstrap_worker_process()
    assert len(calls) == 1
    assert bootstrap_module.is_bootstrapped() is True


def test_bootstrap_worker_process_force_reset_always_runs(
    monkeypatch, _reset_boot_flag,
):
    calls = _stub_bootstrap(monkeypatch)
    dramatiq_app._bootstrap_worker_process()
    dramatiq_app._bootstrap_worker_process(force_reset=True)
    dramatiq_app._bootstrap_worker_process(force_reset=True)
    assert len(calls) == 3
    # In force_reset il flag e' azzerato prima della chiamata.
    assert calls == [False, False, False]


# _BootstrapMiddleware.before_worker_boot: post-fork + ordering


def test_before_worker_boot_discards_pool_then_bootstraps(
    monkeypatch, _reset_boot_flag,
):
    sequence: List[str] = []

    def _fake_discard():
        sequence.append("discard")

    def _fake_bootstrap():
        sequence.append("bootstrap")
        bootstrap_module._BOOT_COMPLETE = True

    monkeypatch.setattr(pg_connection, "discard_pool_after_fork", _fake_discard)
    monkeypatch.setattr(bootstrap_module, "bootstrap", _fake_bootstrap)

    # Simula il post-fork: il child eredita il flag True dal supervisor.
    bootstrap_module._BOOT_COMPLETE = True
    dramatiq_app._BootstrapMiddleware().before_worker_boot(object(), object())

    # Drop del pool PRIMA del bootstrap (altrimenti bootstrap riuserebbe il
    # socket TLS ereditato), e il bootstrap RI-esegue nonostante il flag True.
    assert sequence == ["discard", "bootstrap"]
    assert bootstrap_module.is_bootstrapped() is True


def test_before_worker_boot_propagates_bootstrap_exception(
    monkeypatch, _reset_boot_flag,
):
    monkeypatch.setattr(
        pg_connection, "discard_pool_after_fork", lambda: None
    )

    def _failing_bootstrap():
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr(bootstrap_module, "bootstrap", _failing_bootstrap)

    with pytest.raises(RuntimeError, match="simulated DB unreachable"):
        dramatiq_app._BootstrapMiddleware().before_worker_boot(
            object(), object()
        )
    assert bootstrap_module.is_bootstrapped() is False


def test_bootstrap_middleware_registered_on_broker():
    names = {type(m).__name__ for m in dramatiq_app.broker.middleware}
    assert "_BootstrapMiddleware" in names, (
        "il broker Dramatiq deve avere il middleware di bootstrap "
        "(before_worker_boot) — rimpiazzo del signal Celery worker_init"
    )
