"""Contract test — scripts/load_secrets.py (wrapper exec cloud-agnostic).

Wrapper che fetcha secrets dal backend configurato (SSM/Key Vault/
Secret Manager via factory cloud_backends), popola os.environ, poi
execv il target script.

1. `fetch_secrets` delega a `app.shared.cloud_backends.get_secrets_backend()`
2. `inject_into_environ` NON sovrascrive env gia' presenti (env priority)
3. CLI exit codes: args mancanti -> 2, target non esistente -> 2
4. Errore backend -> exit 1 con log
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
_SCRIPT_PATH = _SCRIPTS_DIR / "load_secrets.py"


@pytest.fixture(scope="module")
def ssm_mod():
    spec = importlib.util.spec_from_file_location("load_secrets_script", _SCRIPT_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["load_secrets_script"] = mod
    spec.loader.exec_module(mod)
    return mod


class _FakeSecretsBackend:
    """Stub del Protocol SecretsBackend per i test."""
    def __init__(self, response=None, raise_on_call=None):
        self._response = response or {}
        self._raise = raise_on_call

    def get_secrets(self, prefix: str) -> dict:
        if self._raise:
            raise self._raise
        return dict(self._response)


# =============================================================================
# 1. fetch_secrets — delega al backend
# =============================================================================


def test_fetch_secrets_delegates_to_backend(ssm_mod):
    fake = _FakeSecretsBackend({
        "DATABASE_URL": "postgresql://u:p@h/db",
        "REDIS_URL": "redis://r:6379",
    })
    with patch("app.shared.cloud_backends.get_secrets_backend", return_value=fake):
        secrets = ssm_mod.fetch_secrets()
    assert secrets == {
        "DATABASE_URL": "postgresql://u:p@h/db",
        "REDIS_URL": "redis://r:6379",
    }


def test_fetch_secrets_backend_error_exits_1(ssm_mod):
    """Qualunque errore dal backend (network, permessi) -> exit 1."""
    fake = _FakeSecretsBackend(raise_on_call=RuntimeError("backend down"))
    with patch("app.shared.cloud_backends.get_secrets_backend", return_value=fake):
        with pytest.raises(SystemExit) as exc:
            ssm_mod.fetch_secrets()
        assert exc.value.code == 1


def test_fetch_secrets_empty_returns_empty_dict(ssm_mod):
    fake = _FakeSecretsBackend({})
    with patch("app.shared.cloud_backends.get_secrets_backend", return_value=fake):
        assert ssm_mod.fetch_secrets() == {}


# =============================================================================
# 2. inject_into_environ — priorita' env pre-esistente
# =============================================================================


def test_inject_sets_new_keys(ssm_mod, monkeypatch):
    monkeypatch.delenv("TEST_NEW_KEY", raising=False)
    count = ssm_mod.inject_into_environ({"TEST_NEW_KEY": "value_from_backend"})
    assert count == 1
    assert os.environ["TEST_NEW_KEY"] == "value_from_backend"


def test_inject_does_not_overwrite_existing_env(ssm_mod, monkeypatch):
    """Env gia' settato ha priorita': utile in dev con override locale."""
    monkeypatch.setenv("TEST_EXISTING_KEY", "from_env")
    count = ssm_mod.inject_into_environ({"TEST_EXISTING_KEY": "from_backend"})
    assert count == 0
    assert os.environ["TEST_EXISTING_KEY"] == "from_env"


def test_inject_mixed_new_and_existing(ssm_mod, monkeypatch):
    monkeypatch.setenv("PRE_EXISTING", "old")
    monkeypatch.delenv("NEW_KEY", raising=False)
    count = ssm_mod.inject_into_environ({
        "PRE_EXISTING": "would_override",
        "NEW_KEY": "fresh",
    })
    assert count == 1
    assert os.environ["PRE_EXISTING"] == "old"
    assert os.environ["NEW_KEY"] == "fresh"


def test_inject_empty_dict_is_noop(ssm_mod):
    assert ssm_mod.inject_into_environ({}) == 0


# =============================================================================
# 3. CLI exit codes
# =============================================================================


def test_main_rejects_missing_args(ssm_mod, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["load_secrets.py"])
    with pytest.raises(SystemExit) as exc:
        ssm_mod.main()
    assert exc.value.code == 2


def test_main_rejects_nonexistent_target(ssm_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys, "argv",
        ["load_secrets.py", str(tmp_path / "does_not_exist.py")],
    )
    with pytest.raises(SystemExit) as exc:
        ssm_mod.main()
    assert exc.value.code == 2


# =============================================================================
# 4. execv behavior
# =============================================================================


def test_main_calls_execv_with_target(ssm_mod, monkeypatch, tmp_path):
    """Dopo il fetch + inject, deve chiamare os.execv sul target."""
    target = tmp_path / "target.py"
    target.write_text("# dummy")

    monkeypatch.setattr(
        sys, "argv",
        ["load_secrets.py", str(target), "--flag", "value"],
    )

    execv_calls = []

    def _fake_execv(path, argv):
        execv_calls.append((path, argv))
        raise SystemExit(0)

    fake = _FakeSecretsBackend({})
    with patch("app.shared.cloud_backends.get_secrets_backend", return_value=fake):
        with patch("os.execv", side_effect=_fake_execv):
            with pytest.raises(SystemExit):
                ssm_mod.main()

    assert len(execv_calls) == 1
    path, argv = execv_calls[0]
    assert path == sys.executable
    assert argv[0] == sys.executable
    assert argv[1] == str(target)
    assert argv[2:] == ["--flag", "value"]


def test_main_injects_secrets_before_exec(ssm_mod, monkeypatch, tmp_path):
    """os.environ deve contenere i secret PRIMA di execv."""
    monkeypatch.delenv("TEST_INJECTED_SECRET", raising=False)

    target = tmp_path / "target.py"
    target.write_text("# dummy")
    monkeypatch.setattr(sys, "argv", ["load_secrets.py", str(target)])

    env_at_exec = {}

    def _fake_execv(path, argv):
        env_at_exec["TEST_INJECTED_SECRET"] = os.environ.get("TEST_INJECTED_SECRET")
        raise SystemExit(0)

    fake = _FakeSecretsBackend({"TEST_INJECTED_SECRET": "yes"})
    with patch("app.shared.cloud_backends.get_secrets_backend", return_value=fake):
        with patch("os.execv", side_effect=_fake_execv):
            with pytest.raises(SystemExit):
                ssm_mod.main()

    assert env_at_exec["TEST_INJECTED_SECRET"] == "yes"
