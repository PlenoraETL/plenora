"""Contract test BE-GO-READY-BOOTSTRAP-ENGINE-002-EXPLICIT-BOOTSTRAP-FUNCTION.

Source-bound + behavioural test for `app.bootstrap.bootstrap()`. The
function replaces the 4 implicit top-level side effects in
`app.main` with a single named boundary. This test locks:

1. **Call order**: `validate_startup → validate_storage_config →
   init_all_dbs → _repair_psycopg2_ssl_env`. Reordering would
   break the comment-encoded contract from `main.py`.
2. **SSL env cleanup runs after init_all_dbs**: psycopg2 sets the
   three SSL env vars when it opens its first connection; popping
   before init would be a no-op.
3. **Idempotency**: calling `bootstrap()` twice has the same
   effect as calling it once (matches the underlying
   `validate_startup` / `init_all_dbs` idempotence).
4. **Exception propagation**: each step's exception type is
   preserved (no wrapping, no logging, no remapping by `bootstrap`).
5. **`main.py` delegates to bootstrap**: source-bound check that
   the top-level block is replaced by a single `_bootstrap()` call
   and the 4 inline statements are gone.
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
from unittest.mock import patch

import pytest

import app.bootstrap as bootstrap_module


pytestmark = [pytest.mark.unit, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOTSTRAP_PY = _BACKEND_ROOT / "app" / "bootstrap.py"
_MAIN_PY = _BACKEND_ROOT / "app" / "main.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Call order — `bootstrap()` invokes the 4 steps in canonical sequence
# ---------------------------------------------------------------------------


def _real_bootstrap():
    """Resolve the un-stubbed bootstrap (conftest no-ops it for tests).

    The session-level conftest replaces `app.bootstrap.bootstrap` with
    a no-op so importing `app.main` doesn't try to touch DB/Redis.
    These tests need the *real* function. The original is stashed at
    `_original_bootstrap` per the conftest contract.
    """
    return getattr(
        bootstrap_module, "_original_bootstrap", bootstrap_module.bootstrap
    )


def test_bootstrap_invokes_four_steps_in_canonical_order(monkeypatch):
    """validate_startup → validate_storage_config → init_all_dbs →
    _repair_psycopg2_ssl_env. Drift = the comment-encoded contract
    from `main.py` is broken."""
    calls: List[str] = []

    def _record_settings_validate():
        calls.append("validate_startup")

    def _record_storage_validate():
        calls.append("validate_storage_config")

    def _record_init_all_dbs():
        calls.append("init_all_dbs")

    def _record_ssl_repair():
        calls.append("_repair_psycopg2_ssl_env")

    # Patch the symbols that `bootstrap()` imports lazily.
    fake_settings = type(
        "S", (), {"validate_startup": staticmethod(_record_settings_validate)}
    )
    monkeypatch.setattr("app.config.settings", fake_settings)
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config",
        _record_storage_validate,
    )
    monkeypatch.setattr(
        "app.db.db_service.init_all_dbs", _record_init_all_dbs
    )
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", _record_ssl_repair
    )

    _real_bootstrap()()

    assert calls == [
        "validate_startup",
        "validate_storage_config",
        "init_all_dbs",
        "_repair_psycopg2_ssl_env",
    ], f"Unexpected boot order: {calls}"


def test_bootstrap_signature_returns_none(monkeypatch):
    """Contract: `bootstrap() -> None`. No return value, no
    parameters. Caller must not depend on a return."""
    monkeypatch.setattr(
        "app.config.settings",
        type("S", (), {"validate_startup": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None
    )
    monkeypatch.setattr("app.db.db_service.init_all_dbs", lambda: None)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None
    )

    result = _real_bootstrap()()
    assert result is None


# ---------------------------------------------------------------------------
# 2. SSL env cleanup runs AFTER init_all_dbs
# ---------------------------------------------------------------------------


def test_ssl_env_cleanup_runs_after_init_all_dbs(monkeypatch):
    """The 3 SSL env vars must be popped only after `init_all_dbs()`
    runs. psycopg2 leaks them on first connect; popping before would
    be a no-op."""
    timeline: List[str] = []

    def _record_init_all_dbs():
        timeline.append("init_all_dbs:enter")
        # Simulate psycopg2 polluting env DURING init_all_dbs.
        os.environ["REQUESTS_CA_BUNDLE"] = "/some/cert.pem"
        os.environ["SSL_CERT_FILE"] = "/some/cert.pem"
        os.environ["CURL_CA_BUNDLE"] = "/some/cert.pem"
        timeline.append("init_all_dbs:exit")

    def _record_ssl_repair():
        timeline.append("ssl_repair:enter")
        # Snapshot the env state observed at this point.
        for name in (
            "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"
        ):
            timeline.append(f"saw:{name}={name in os.environ}")
        # Real implementation pops them.
        for name in (
            "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"
        ):
            os.environ.pop(name, None)
        timeline.append("ssl_repair:exit")

    monkeypatch.setattr(
        "app.config.settings",
        type("S", (), {"validate_startup": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None
    )
    monkeypatch.setattr("app.db.db_service.init_all_dbs", _record_init_all_dbs)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", _record_ssl_repair
    )

    _real_bootstrap()()

    # Order: init_all_dbs both events come before ssl_repair events.
    init_exit_idx = timeline.index("init_all_dbs:exit")
    ssl_enter_idx = timeline.index("ssl_repair:enter")
    assert init_exit_idx < ssl_enter_idx

    # When ssl_repair ran, all 3 SSL env vars were observable
    # (proving init_all_dbs ran first AND populated them).
    assert "saw:REQUESTS_CA_BUNDLE=True" in timeline
    assert "saw:SSL_CERT_FILE=True" in timeline
    assert "saw:CURL_CA_BUNDLE=True" in timeline


def test_repair_psycopg2_ssl_env_pops_three_keys():
    """Direct contract: `_repair_psycopg2_ssl_env()` must pop exactly
    `REQUESTS_CA_BUNDLE`, `SSL_CERT_FILE`, `CURL_CA_BUNDLE` from
    `os.environ`. Idempotent (uses `pop(..., None)`)."""
    saved = {
        name: os.environ.get(name)
        for name in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE")
    }
    try:
        os.environ["REQUESTS_CA_BUNDLE"] = "x"
        os.environ["SSL_CERT_FILE"] = "y"
        os.environ["CURL_CA_BUNDLE"] = "z"

        bootstrap_module._repair_psycopg2_ssl_env()

        assert "REQUESTS_CA_BUNDLE" not in os.environ
        assert "SSL_CERT_FILE" not in os.environ
        assert "CURL_CA_BUNDLE" not in os.environ

        # Idempotency: second call is a no-op.
        bootstrap_module._repair_psycopg2_ssl_env()
        assert "REQUESTS_CA_BUNDLE" not in os.environ
    finally:
        # Restore prior state.
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_repair_psycopg2_ssl_env_does_not_touch_other_vars(monkeypatch):
    """Whitelist: only the 3 named keys are popped; other env vars
    are untouched. Source-bound + behavioural."""
    monkeypatch.setenv("UNRELATED_VAR", "keepme")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "to-be-popped")

    bootstrap_module._repair_psycopg2_ssl_env()

    assert os.environ.get("UNRELATED_VAR") == "keepme"
    assert "REQUESTS_CA_BUNDLE" not in os.environ


# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------


def test_bootstrap_is_idempotent(monkeypatch):
    """Calling `bootstrap()` twice has the same effect as once. The
    underlying constituents are idempotent (validate_startup is FS
    safe; init_all_dbs uses CREATE IF NOT EXISTS); `bootstrap` does
    not add state of its own."""
    counts = {"validate_startup": 0, "validate_storage_config": 0,
              "init_all_dbs": 0, "ssl_repair": 0}

    def _bump(name):
        def _inner():
            counts[name] += 1
        return _inner

    fake_settings = type(
        "S", (), {"validate_startup": staticmethod(_bump("validate_startup"))}
    )
    monkeypatch.setattr("app.config.settings", fake_settings)
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config",
        _bump("validate_storage_config"),
    )
    monkeypatch.setattr(
        "app.db.db_service.init_all_dbs", _bump("init_all_dbs")
    )
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", _bump("ssl_repair")
    )

    _real_bootstrap()()
    _real_bootstrap()()

    # Each step ran exactly twice (one per bootstrap call). No
    # unexpected duplication or skip.
    assert counts == {
        "validate_startup": 2,
        "validate_storage_config": 2,
        "init_all_dbs": 2,
        "ssl_repair": 2,
    }


# ---------------------------------------------------------------------------
# 4. Exception propagation — each step's exception type preserved
# ---------------------------------------------------------------------------


def test_bootstrap_propagates_validate_startup_systemexit(monkeypatch):
    """validate_startup exits with SystemExit(1) when DATA_DIR is
    unwritable or Redis is unreachable on server. `bootstrap()` must
    not swallow it."""
    def _exit_one():
        raise SystemExit(1)

    monkeypatch.setattr(
        "app.config.settings",
        type("S", (), {"validate_startup": staticmethod(_exit_one)}),
    )
    # Following steps would raise too; we want to assert they don't
    # run (because the first step raised).
    other_called = {"flag": False}
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config",
        lambda: other_called.__setitem__("flag", True),
    )
    monkeypatch.setattr("app.db.db_service.init_all_dbs", lambda: None)
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None
    )

    with pytest.raises(SystemExit) as exc_info:
        _real_bootstrap()()
    assert exc_info.value.code == 1
    assert other_called["flag"] is False, (
        "validate_storage_config must NOT run after validate_startup raised."
    )


def test_bootstrap_propagates_storage_config_error(monkeypatch):
    """validate_storage_config raises StorageConfigError on
    incoherent combos. `bootstrap()` must not wrap or remap it."""
    from app.shared.cloud_backends import StorageConfigError

    def _bad_storage():
        raise StorageConfigError("bad combo")

    monkeypatch.setattr(
        "app.config.settings",
        type("S", (), {"validate_startup": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", _bad_storage
    )

    init_called = {"flag": False}
    monkeypatch.setattr(
        "app.db.db_service.init_all_dbs",
        lambda: init_called.__setitem__("flag", True),
    )
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env", lambda: None
    )

    with pytest.raises(StorageConfigError, match="bad combo"):
        _real_bootstrap()()
    assert init_called["flag"] is False, (
        "init_all_dbs must NOT run after validate_storage_config raised."
    )


def test_bootstrap_propagates_init_all_dbs_error(monkeypatch):
    """init_all_dbs may re-raise psycopg2.Error on connection
    failure. Use a generic Exception subclass to avoid pulling in
    a real PG dependency."""
    class _FakeDBError(Exception):
        pass

    def _bad_init():
        raise _FakeDBError("connection refused")

    monkeypatch.setattr(
        "app.config.settings",
        type("S", (), {"validate_startup": staticmethod(lambda: None)}),
    )
    monkeypatch.setattr(
        "app.shared.cloud_backends.validate_storage_config", lambda: None
    )
    monkeypatch.setattr("app.db.db_service.init_all_dbs", _bad_init)

    ssl_called = {"flag": False}
    monkeypatch.setattr(
        "app.bootstrap._repair_psycopg2_ssl_env",
        lambda: ssl_called.__setitem__("flag", True),
    )

    with pytest.raises(_FakeDBError, match="connection refused"):
        _real_bootstrap()()
    assert ssl_called["flag"] is False, (
        "_repair_psycopg2_ssl_env must NOT run after init_all_dbs raised."
    )


# ---------------------------------------------------------------------------
# 5. main.py delegates to bootstrap (source-bound)
# ---------------------------------------------------------------------------


def test_main_py_imports_bootstrap_and_calls_it_at_module_load():
    """`main.py` must contain a single call to the bootstrap boundary
    at module level. The 4 inline statements must be gone."""
    text = _read(_MAIN_PY)

    # Import + call present.
    assert "from app.bootstrap import bootstrap" in text, (
        "main.py must import the bootstrap boundary."
    )
    # Call site exists at module level (not inside a function/class).
    # Match pattern: `_bootstrap()` or `bootstrap()` at indent 0 anywhere
    # before the FastAPI app construction.
    bootstrap_call = re.search(r"^_bootstrap\(\)$", text, flags=re.MULTILINE)
    assert bootstrap_call is not None, (
        "main.py must call `_bootstrap()` at module level."
    )


def _strip_line_comments(text: str) -> str:
    """Strip `#` line comments so substring assertions only inspect
    code, not docs/explanatory comments."""
    out_lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Inline `... # comment` — keep the code part.
        if "#" in line:
            # Naive split (no string-aware): good enough for main.py
            # which doesn't put `#` inside string literals at the
            # module-level statements we're checking.
            line = line.split("#", 1)[0]
        out_lines.append(line)
    return "\n".join(out_lines)


def test_main_py_no_longer_inlines_the_four_steps():
    """The 4 statements that bootstrap() now owns must be removed
    from `main.py`'s top-level (comments mentioning the names are OK)."""
    code_only = _strip_line_comments(_read(_MAIN_PY))

    # `settings.validate_startup()` should no longer appear inline.
    assert "settings.validate_startup()" not in code_only, (
        "main.py must delegate validate_startup() to bootstrap()."
    )
    # `validate_storage_config()` likewise.
    assert "validate_storage_config()" not in code_only, (
        "main.py must delegate validate_storage_config() to bootstrap()."
    )
    # `init_all_dbs()` likewise.
    assert "init_all_dbs()" not in code_only, (
        "main.py must delegate init_all_dbs() to bootstrap()."
    )
    # The 3 SSL pops must not be inline anymore.
    for key in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        assert f"os.environ.pop('{key}'" not in code_only, (
            f"main.py must delegate the {key} pop to bootstrap()."
        )
        assert f'os.environ.pop("{key}"' not in code_only


def test_bootstrap_module_does_not_import_main():
    """Boundary purity: `app.bootstrap` must not import `app.main`
    (would create a cycle and defeat the testability goal)."""
    text = _read(_BOOTSTRAP_PY)
    assert "from app.main" not in text
    assert "import app.main" not in text


def test_bootstrap_module_uses_lazy_imports_for_heavy_modules():
    """The four heavy collaborators (settings, validate_storage_config,
    init_all_dbs) should be imported lazily inside `bootstrap()` so
    that `import app.bootstrap` itself remains cheap and side-effect
    free."""
    text = _read(_BOOTSTRAP_PY)
    # Ensure the imports do NOT appear at module top level (above
    # `def bootstrap`). The split point is `def bootstrap`.
    head, _, _ = text.partition("def bootstrap")
    for forbidden in (
        "from app.config import settings",
        "from app.shared.cloud_backends import validate_storage_config",
        "from app.db.db_service import init_all_dbs",
    ):
        assert forbidden not in head, (
            f"`{forbidden}` must be a lazy import inside bootstrap(), "
            f"not at module level."
        )
