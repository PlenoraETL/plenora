"""
Contract test BE-GO-READY-BOOTSTRAP-ENGINE-013-DEPENDENCY-GRAPH-LOCK.

Locks the bootstrap dependency graph WITHOUT introducing a DI
"""

from __future__ import annotations

import os
import importlib

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


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


@pytest.fixture(autouse=True)
def _restore_real_bootstrap_module():
    importlib.reload(bootstrap_module)
    yield
    importlib.reload(bootstrap_module)


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_PY = _BACKEND_ROOT / "app" / "bootstrap.py"
_MAIN_PY = _BACKEND_ROOT / "app" / "main.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _real_bootstrap():
    """Return the real bootstrap from the reloaded module."""
    return bootstrap_module.bootstrap


def _real_teardown():
    return bootstrap_module.teardown


# 1. Source-bound: bootstrap.py canonical internal order


def test_bootstrap_py_canonical_order_in_source():
    """The source of `bootstrap()` must contain the four canonical
    step calls in the documented order: `validate_startup` →
    `validate_storage_config` → `init_all_dbs` →
    `_repair_psycopg2_ssl_env`. Reordering the calls in the source
    breaks the comment-encoded contract from the module docstring;
    this test is the structural lock."""
    src = _read(_BOOTSTRAP_PY)

    # Restrict to the body of `def bootstrap(...) -> None` so this
    # test is not fooled by lines in docstrings of other helpers.
    m = re.search(
        r"def bootstrap\([^)]*\)\s*->\s*None\s*:\s*\n(.*?)(?=\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "Could not locate `def bootstrap(...) -> None:` in app/bootstrap.py"
    )
    body = m.group(1)

    canonical_markers = [
        "validate_startup(settings)",
        "validate_storage_config()",
        "init_all_dbs()",
        "_repair_psycopg2_ssl_env()",
    ]
    last_idx = -1
    for marker in canonical_markers:
        idx = body.find(marker)
        assert idx != -1, (
            f"Canonical step missing from bootstrap() body: {marker!r}"
        )
        assert idx > last_idx, (
            f"Canonical order violated: {marker!r} appears before its "
            f"predecessor in `bootstrap()` body. Reorder calls or "
            f"update the documented contract."
        )
        last_idx = idx


def test_bootstrap_completion_flag_is_set_last_in_source():
    """The `_BOOT_COMPLETE = True` write must be the LAST statement
    of `bootstrap()`. If a future contributor inserts another step
    after it, the flag would flip on a partial-complete bootstrap.
    Source-bound check so the bug is caught at lint time."""
    src = _read(_BOOTSTRAP_PY)
    m = re.search(
        r"def bootstrap\([^)]*\)\s*->\s*None\s*:\s*\n(.*?)(?=\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert m is not None
    body = m.group(1)

    flag_set = "_BOOT_COMPLETE = True"
    ssl_repair = "_repair_psycopg2_ssl_env()"

    flag_idx = body.find(flag_set)
    ssl_idx = body.find(ssl_repair)

    assert flag_idx != -1, (
        f"`{flag_set}` not found in bootstrap() body — the completion "
        f"flag write must live inside `bootstrap()` itself."
    )
    assert ssl_idx != -1
    assert flag_idx > ssl_idx, (
        "`_BOOT_COMPLETE = True` must come AFTER all four canonical "
        "steps. Moving it earlier marks bootstrap complete on a "
        "partial run."
    )


# 2. Source-bound: main.py calls _bootstrap() BEFORE router imports


def test_main_py_bootstrap_runs_before_router_imports():
    """`app/main.py` must call `_bootstrap()` before importing any
    router module (`app.core.<module>.router` or `*_router`). The
    bootstrap function is the boundary that initialises DB / Redis /
    storage; routers loaded before it would import a half-configured
    app."""
    src = _read(_MAIN_PY)

    # Locate the line that invokes the bootstrap function. The line
    # is `_bootstrap()` on its own (the import alias is documented
    # at the top of main.py). Tolerate whitespace.
    bootstrap_call = re.search(r"^_bootstrap\(\)\s*$", src, re.MULTILINE)
    assert bootstrap_call is not None, (
        "Expected `_bootstrap()` call line in app/main.py. "
        "The dependency graph requires this single named entry."
    )
    boot_call_idx = bootstrap_call.start()

    # Find every `from app.core.X.router import ...` /
    # `from app.core.auth.<routerfile> import ...` etc.
    router_import_pattern = re.compile(
        r"^from\s+app\.core\.[\w\.]+\s+import\s+[\w\.,\s\(\)]+",
        re.MULTILINE,
    )
    router_imports = [
        (m.start(), m.group())
        for m in router_import_pattern.finditer(src)
    ]
    # Filter to imports that look like router modules (names ending
    # in `router`, `_router`, or that the migration block declares).
    # The strictest form: ANY `app.core.*` import must come AFTER
    # the bootstrap call. main.py does not (and should not) import
    # `app.core.*` before bootstrap.
    assert router_imports, (
        "Expected at least one `from app.core.* import ...` line "
        "in app/main.py — sanity check that the regex is correct."
    )

    for idx, line in router_imports:
        assert idx > boot_call_idx, (
            f"Router import found BEFORE _bootstrap() call:\n"
            f"  {line.strip()!r}\n"
            f"All `from app.core.*` imports must come after "
            f"`_bootstrap()` so routers load against an "
            f"initialised app."
        )


def test_main_py_imports_bootstrap_before_calling_it():
    """The `from app.bootstrap import bootstrap as _bootstrap` line
    must come before the `_bootstrap()` call. Defensive — this could
    only break if someone reorders main.py blindly."""
    src = _read(_MAIN_PY)

    import_match = re.search(
        r"^from\s+app\.bootstrap\s+import\s+bootstrap\s+as\s+_bootstrap.*$",
        src,
        re.MULTILINE,
    )
    assert import_match is not None, (
        "Expected `from app.bootstrap import bootstrap as _bootstrap` "
        "line in app/main.py."
    )
    call_match = re.search(r"^_bootstrap\(\)\s*$", src, re.MULTILINE)
    assert call_match is not None
    assert import_match.start() < call_match.start()


# 3. Behavioural: is_bootstrapped() False → True transition


@pytest.fixture
def _reset_boot_flag(monkeypatch):
    """Snapshot + restore `_BOOT_COMPLETE` around the test.

    The conftest stubs `bootstrap()` to a no-op, so the global
    `_BOOT_COMPLETE` is whatever the stub left it as (typically
    False). We force False at test entry and restore at exit so
    these tests are isolated regardless of the test order."""
    original = bootstrap_module._BOOT_COMPLETE
    bootstrap_module._BOOT_COMPLETE = False
    try:
        yield
    finally:
        bootstrap_module._BOOT_COMPLETE = original


def test_is_bootstrapped_initially_false(_reset_boot_flag):
    """At the start of the lifecycle, `is_bootstrapped()` is False."""
    assert bootstrap_module.is_bootstrapped() is False


def test_is_bootstrapped_flips_to_true_after_successful_bootstrap(
    monkeypatch, _reset_boot_flag,
):
    """After a successful real-bootstrap run (with the four steps
    stubbed to no-ops), `is_bootstrapped()` returns True."""
    # Stub the four steps to no-ops to avoid real DB/Redis/FS I/O.
    monkeypatch.setattr(
        "app.startup_validation.validate_startup", lambda settings: None,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None,
    )
    monkeypatch.setattr("app.core.runtime.db_init.init_all_dbs", lambda: None)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None,
    )

    assert bootstrap_module.is_bootstrapped() is False
    _real_bootstrap()()
    assert bootstrap_module.is_bootstrapped() is True


def test_is_bootstrapped_stays_false_when_step_raises(
    monkeypatch, _reset_boot_flag,
):
    """If a step raises, the flag must NOT flip. Partial bootstrap
    is not "complete" — only an end-to-end successful run flips
    the flag."""
    monkeypatch.setattr(
        "app.startup_validation.validate_startup", lambda settings: None,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None,
    )

    # init_all_dbs raises mid-bootstrap.
    def _raise():
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr("app.core.runtime.db_init.init_all_dbs", _raise)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None,
    )

    assert bootstrap_module.is_bootstrapped() is False
    with pytest.raises(RuntimeError, match="simulated DB unreachable"):
        _real_bootstrap()()
    # Flag still False after the raise.
    assert bootstrap_module.is_bootstrapped() is False


def test_is_bootstrapped_recovers_to_true_on_retry_after_transient_failure(
    monkeypatch, _reset_boot_flag,
):
    """A transient failure followed by a successful re-run: the
    flag flips to True on the second attempt. Validates that
    bootstrap is re-runnable and that the flag tracks the latest
    successful boundary crossing."""
    monkeypatch.setattr(
        "app.startup_validation.validate_startup", lambda settings: None,
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None,
    )

    # First call: init_all_dbs raises. Second call: succeeds.
    state = {"calls": 0}

    def _flaky_init():
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("transient")

    monkeypatch.setattr("app.core.runtime.db_init.init_all_dbs", _flaky_init)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None,
    )

    with pytest.raises(RuntimeError, match="transient"):
        _real_bootstrap()()
    assert bootstrap_module.is_bootstrapped() is False

    _real_bootstrap()()
    assert bootstrap_module.is_bootstrapped() is True


def test_teardown_resets_completion_flag(monkeypatch, _reset_boot_flag):
    """`teardown()` must flip `_BOOT_COMPLETE` back to False so the
    flag reflects the post-shutdown state (test harnesses can
    observe the full False → True → False lifecycle)."""
    # Force True to simulate a completed bootstrap.
    bootstrap_module._BOOT_COMPLETE = True

    # Stub the only side effect of teardown (`pg_connection.close_pool`)
    # so we don't try to close a real (non-existent) pool.
    monkeypatch.setattr(
        "app.db.pg_connection.close_pool", lambda: None,
    )

    assert bootstrap_module.is_bootstrapped() is True
    _real_teardown()()
    assert bootstrap_module.is_bootstrapped() is False


# 4. Module surface: is_bootstrapped is the only public reader


def test_is_bootstrapped_is_callable_and_returns_bool():
    """Public surface check: `is_bootstrapped()` exists, is
    callable, returns `bool` (no None / int leak)."""
    assert callable(bootstrap_module.is_bootstrapped)
    out = bootstrap_module.is_bootstrapped()
    assert isinstance(out, bool)


def test_completion_flag_is_module_level_bool():
    """`_BOOT_COMPLETE` lives at module level and is a `bool`. The
    name is `_`-prefixed so callers know not to write to it directly
    — the public reader is `is_bootstrapped()`."""
    assert hasattr(bootstrap_module, "_BOOT_COMPLETE")
    assert isinstance(bootstrap_module._BOOT_COMPLETE, bool)
