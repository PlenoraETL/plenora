import os
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_TESTS2_CONFTEST_PATH = Path(__file__).resolve().parents[1] / "conftest.py"
_SPEC = importlib.util.spec_from_file_location(
    "tests2_root_conftest_for_temp_contract",
    _TESTS2_CONFTEST_PATH,
)
assert _SPEC is not None
assert _SPEC.loader is not None
tests_conftest = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tests_conftest)


def test_pytest_basetemp_defaults_to_unique_repo_local_run_dir(tmp_path, monkeypatch):
    fake_root = tmp_path / ".pytest-run"
    monkeypatch.setattr(tests_conftest, "_PYTEST_RUN_ROOT", fake_root)

    config = SimpleNamespace(option=SimpleNamespace(basetemp=None))

    tests_conftest._normalize_pytest_basetemp(config)

    basetemp = Path(config.option.basetemp)
    assert basetemp.parent == fake_root
    assert basetemp.name.startswith("run-")


def test_pytest_basetemp_preserves_explicit_user_value(monkeypatch):
    fake_root = Path("C:/repo/backend/.pytest-run")
    monkeypatch.setattr(tests_conftest, "_ensure_pytest_run_root", lambda: fake_root)
    explicit = "C:/explicit/pytest-temp"
    config = SimpleNamespace(option=SimpleNamespace(basetemp=explicit))

    tests_conftest._normalize_pytest_basetemp(config)

    assert config.option.basetemp == explicit


def test_stale_cleanup_is_limited_to_managed_run_dirs(tmp_path, monkeypatch):
    old_run = tmp_path / "run-old"
    old_run.mkdir()
    os.utime(old_run, (1, 1))
    unrelated = tmp_path / "manual-temp"
    unrelated.mkdir()
    monkeypatch.setattr(tests_conftest, "_PYTEST_RUN_ROOT", tmp_path)

    tests_conftest._safe_prune_old_pytest_run_dirs(
        now=tests_conftest._PYTEST_RUN_MAX_AGE_SECONDS + 2
    )

    assert not old_run.exists()
    assert unrelated.exists()
