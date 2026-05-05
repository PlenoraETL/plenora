"""Contract test BE-GO-READY-BOOTSTRAP-ENGINE-014-WORKER-BOOTSTRAP-PARITY.

Lock the worker-side bootstrap boundary. Closes residual R-013.4:
the web boot path calls `app.bootstrap.bootstrap()` once before
router imports, and now Celery worker / beat startup must cross
the SAME boundary.

Three signal handlers wired in `workers.celery_app`:
  - `worker_init`            — main worker process startup (solo /
                                 threads / gevent / prefork master)
  - `worker_process_init`    — each prefork child post-fork
                                 (force_reset=True so inherited PG
                                 pool is replaced)
  - `beat_init`              — beat scheduler startup

Invariants pinned:
  1. Source-bound: `celery_app.py` imports the three signals AND
     defines a `@<signal>.connect` handler for each.
  2. The handlers all delegate to `_bootstrap_worker_process(...)`.
  3. `_bootstrap_worker_process()` is idempotent
     (`is_bootstrapped()` short-circuit).
  4. `_bootstrap_worker_process(force_reset=True)` always re-runs
     bootstrap (post-fork pool re-init).
  5. Fail-closed: an exception from `bootstrap()` propagates out
     of the signal handler (Celery → process exit → supervisor
     restart). The handler does NOT swallow.
  6. `is_bootstrapped()` flips True after a successful worker boot
     and False between forks (force_reset path).

The tests stub `app.bootstrap.bootstrap` to a recorded call (no
real DB / Redis / FS I/O). They exercise the handlers DIRECTLY
(via `celery_app._bootstrap_on_worker_init(...)` etc.) — this
mirrors the existing `test_bootstrap_contract.py` pattern of
calling the un-stubbed function instead of relying on Celery
signal dispatch.
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL is set before any import that may load
# `app.db.pg_connection` (raises RuntimeError at module-level if missing).
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import re
from pathlib import Path
from typing import List

import pytest

import app.bootstrap as bootstrap_module
import workers.celery_app as celery_app_module


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_CELERY_APP_PY = _BACKEND_ROOT / "workers" / "celery_app.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-bound: signals imported and handlers connected
# ---------------------------------------------------------------------------


def test_celery_app_imports_three_bootstrap_signals():
    """Source check: `worker_init`, `worker_process_init` and
    `beat_init` are imported from `celery.signals`. Drift = a
    contributor removed one and broke the parity invariant."""
    src = _read(_CELERY_APP_PY)
    # Match a multi-line `from celery.signals import (...)` block.
    m = re.search(
        r"from\s+celery\.signals\s+import\s+\(([^)]+)\)",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "Expected `from celery.signals import (...)` block in "
        "workers/celery_app.py."
    )
    imported = m.group(1)
    for signal_name in ("worker_init", "worker_process_init", "beat_init"):
        assert signal_name in imported, (
            f"`celery.signals.{signal_name}` not imported in "
            f"workers/celery_app.py. The bootstrap parity contract "
            f"requires all three signals."
        )


def test_celery_app_connects_handler_to_each_bootstrap_signal():
    """Source check: each of the three signals has a
    `@<signal>.connect` decorated handler. Drift = a handler was
    removed or its connect was disconnected."""
    src = _read(_CELERY_APP_PY)
    for signal_name in ("worker_init", "worker_process_init", "beat_init"):
        decorator = f"@{signal_name}.connect"
        assert decorator in src, (
            f"Expected `{decorator}` decorator in "
            f"workers/celery_app.py. Required for bootstrap parity."
        )


def test_handler_for_worker_process_init_uses_force_reset_true():
    """Source check: the `worker_process_init` handler must call
    `_bootstrap_worker_process(force_reset=True)`. Without
    force_reset the post-fork child would skip re-init and reuse
    the parent's broken psycopg2 pool."""
    src = _read(_CELERY_APP_PY)
    # Locate the function body following @worker_process_init.connect.
    m = re.search(
        r"@worker_process_init\.connect\s*\n"
        r"def\s+\w+\([^)]*\)\s*->\s*None\s*:\s*\n"
        r"(?:\s*\"\"\"(?:.*?)\"\"\"\s*\n)?"  # optional docstring
        r"(.*?)(?=\n@|\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate `worker_process_init` handler body in "
        "workers/celery_app.py."
    )
    body = m.group(1)
    assert "force_reset=True" in body, (
        "`worker_process_init` handler must invoke "
        "`_bootstrap_worker_process(force_reset=True)`. "
        "Post-fork children inherit a broken psycopg2 pool; "
        "without force_reset the idempotent short-circuit would "
        "skip the re-init."
    )


def test_handlers_for_worker_init_and_beat_init_do_not_force_reset():
    """Source check: the `worker_init` and `beat_init` handlers
    must NOT pass `force_reset=True`. They run in fresh processes
    with no inherited state."""
    src = _read(_CELERY_APP_PY)
    for signal_name in ("worker_init", "beat_init"):
        m = re.search(
            rf"@{signal_name}\.connect\s*\n"
            rf"def\s+\w+\([^)]*\)\s*->\s*None\s*:\s*\n"
            rf"(?:\s*\"\"\"(?:.*?)\"\"\"\s*\n)?"
            rf"(.*?)(?=\n@|\ndef |\Z)",
            src,
            re.DOTALL,
        )
        assert m is not None, (
            f"Could not locate `{signal_name}` handler body."
        )
        body = m.group(1)
        assert "force_reset=True" not in body, (
            f"`{signal_name}` handler must NOT pass "
            f"force_reset=True. force_reset is the post-fork "
            f"recovery primitive, not a default."
        )


# ---------------------------------------------------------------------------
# Behavioural: handlers actually call bootstrap
# ---------------------------------------------------------------------------


@pytest.fixture
def _reset_boot_flag():
    """Snapshot + restore `_BOOT_COMPLETE` around the test."""
    original = bootstrap_module._BOOT_COMPLETE
    bootstrap_module._BOOT_COMPLETE = False
    try:
        yield
    finally:
        bootstrap_module._BOOT_COMPLETE = original


def _stub_bootstrap(monkeypatch) -> List[bool]:
    """Replace `app.bootstrap.bootstrap` with a recorder that flips
    `_BOOT_COMPLETE` to True (mimicking the real implementation's
    last statement). Returns a list whose length == # of calls and
    values are the `_BOOT_COMPLETE` value SEEN at entry of each
    invocation."""
    calls_seen_flag: List[bool] = []

    def _fake_bootstrap():
        # Capture flag BEFORE we flip it: tests assert pre-call
        # state (force_reset path = saw False; no-force path = also
        # False because the handler short-circuits if True).
        calls_seen_flag.append(bootstrap_module._BOOT_COMPLETE)
        bootstrap_module._BOOT_COMPLETE = True

    monkeypatch.setattr(bootstrap_module, "bootstrap", _fake_bootstrap)
    return calls_seen_flag


def test_worker_init_handler_calls_bootstrap_when_not_bootstrapped(
    monkeypatch, _reset_boot_flag,
):
    """`worker_init` → `_bootstrap_worker_process()` →
    `bootstrap()` runs once, flag flips True."""
    calls = _stub_bootstrap(monkeypatch)
    assert bootstrap_module.is_bootstrapped() is False
    celery_app_module._bootstrap_on_worker_init()
    assert len(calls) == 1
    assert bootstrap_module.is_bootstrapped() is True


def test_worker_init_handler_skips_when_already_bootstrapped(
    monkeypatch, _reset_boot_flag,
):
    """Idempotency: a second `worker_init` (or any
    non-force-reset path) does NOT re-run bootstrap()."""
    calls = _stub_bootstrap(monkeypatch)
    # Pre-flip to simulate "already bootstrapped".
    bootstrap_module._BOOT_COMPLETE = True

    celery_app_module._bootstrap_on_worker_init()
    assert calls == []  # short-circuit; bootstrap NOT re-invoked


def test_worker_process_init_handler_force_resets_and_re_runs(
    monkeypatch, _reset_boot_flag,
):
    """Post-fork: even if the inherited flag says True, the
    handler must reset and re-run bootstrap (PG pool re-init)."""
    calls = _stub_bootstrap(monkeypatch)
    # Simulate post-fork: child inherits flag=True from parent.
    bootstrap_module._BOOT_COMPLETE = True

    celery_app_module._bootstrap_on_worker_process_init()
    # Must have run exactly once, and at entry the flag was False
    # because the handler reset it before the bootstrap call.
    assert len(calls) == 1
    assert calls[0] is False
    # Final state: True again after the re-run.
    assert bootstrap_module.is_bootstrapped() is True


def test_beat_init_handler_calls_bootstrap_once(
    monkeypatch, _reset_boot_flag,
):
    """`beat_init` mirrors `worker_init` semantics."""
    calls = _stub_bootstrap(monkeypatch)
    assert bootstrap_module.is_bootstrapped() is False
    celery_app_module._bootstrap_on_beat_init()
    assert len(calls) == 1
    assert bootstrap_module.is_bootstrapped() is True


def test_beat_init_handler_skips_when_already_bootstrapped(
    monkeypatch, _reset_boot_flag,
):
    calls = _stub_bootstrap(monkeypatch)
    bootstrap_module._BOOT_COMPLETE = True
    celery_app_module._bootstrap_on_beat_init()
    assert calls == []


# ---------------------------------------------------------------------------
# Fail-closed: bootstrap exception propagates from the handler
# ---------------------------------------------------------------------------


def test_worker_process_init_propagates_bootstrap_exception(
    monkeypatch, _reset_boot_flag,
):
    """If `bootstrap()` raises, the worker handler MUST re-raise.
    Celery treats raised signal handler exceptions as fatal →
    process exit → supervisor restart. Swallowing here would let
    the worker accept tasks against a broken DB / Redis / storage
    config."""
    def _failing_bootstrap():
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr(bootstrap_module, "bootstrap", _failing_bootstrap)

    with pytest.raises(RuntimeError, match="simulated DB unreachable"):
        celery_app_module._bootstrap_on_worker_process_init()
    # Flag stays False (or whatever it was before force_reset wrote
    # False — never True).
    assert bootstrap_module.is_bootstrapped() is False


def test_worker_init_propagates_bootstrap_exception(
    monkeypatch, _reset_boot_flag,
):
    def _failing_bootstrap():
        raise RuntimeError("simulated storage config error")

    monkeypatch.setattr(bootstrap_module, "bootstrap", _failing_bootstrap)

    with pytest.raises(RuntimeError, match="simulated storage config error"):
        celery_app_module._bootstrap_on_worker_init()
    assert bootstrap_module.is_bootstrapped() is False


def test_beat_init_propagates_bootstrap_exception(
    monkeypatch, _reset_boot_flag,
):
    def _failing_bootstrap():
        raise RuntimeError("simulated validate_startup failure")

    monkeypatch.setattr(bootstrap_module, "bootstrap", _failing_bootstrap)

    with pytest.raises(RuntimeError, match="simulated validate_startup failure"):
        celery_app_module._bootstrap_on_beat_init()
    assert bootstrap_module.is_bootstrapped() is False


# ---------------------------------------------------------------------------
# _bootstrap_worker_process direct API contract
# ---------------------------------------------------------------------------


def test_bootstrap_worker_process_default_is_idempotent(
    monkeypatch, _reset_boot_flag,
):
    """Direct API: calling `_bootstrap_worker_process()` twice in
    a row runs `bootstrap()` once."""
    calls = _stub_bootstrap(monkeypatch)
    celery_app_module._bootstrap_worker_process()
    celery_app_module._bootstrap_worker_process()
    assert len(calls) == 1


def test_bootstrap_worker_process_force_reset_always_runs(
    monkeypatch, _reset_boot_flag,
):
    """Direct API: `force_reset=True` always re-runs bootstrap
    (the post-fork primitive)."""
    calls = _stub_bootstrap(monkeypatch)
    celery_app_module._bootstrap_worker_process()  # 1st run
    celery_app_module._bootstrap_worker_process(force_reset=True)  # 2nd run
    celery_app_module._bootstrap_worker_process(force_reset=True)  # 3rd run
    assert len(calls) == 3
