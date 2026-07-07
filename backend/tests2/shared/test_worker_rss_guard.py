"""
Contract test BE-GO-READY-MEMORY-OOM-002-WORKER-RSS-SAMPLING.

Tests:
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.resources.exceptions import MemoryLimitExceededError
from tests2.shared._ast_identifiers import imports_module

pytestmark = [pytest.mark.unit, pytest.mark.contract]


# 1. Helper purity

def test_memory_guard_module_does_not_import_fastapi():
    """memory_guard.py must be pure — no FastAPI/starlette imports."""
    src = (Path(__file__).resolve().parent.parent.parent
           / "app" / "core" / "resources" / "memory_guard.py")
    text = src.read_text(encoding="utf-8")
    assert not imports_module(text, "fastapi")
    assert not imports_module(text, "starlette")


# 2. Guard behavior

def test_check_worker_rss_raises_when_above_cap():
    """RSS above cap -> MemoryLimitExceededError."""
    from app.core.resources.memory_guard import check_worker_rss

    with patch("app.core.resources.memory_guard.get_current_rss_bytes", return_value=2_000_000_000):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            check_worker_rss(cap_bytes=1_500_000_000, stage="pipeline_execute")

        assert exc_info.value.observed_bytes == 2_000_000_000
        assert exc_info.value.limit_bytes == 1_500_000_000
        assert exc_info.value.stage == "pipeline_execute"
        assert exc_info.value.path_basename == "worker_process"


def test_check_worker_rss_passes_when_below_cap():
    """RSS below cap -> no exception."""
    from app.core.resources.memory_guard import check_worker_rss

    with patch("app.core.resources.memory_guard.get_current_rss_bytes", return_value=500_000_000):
        check_worker_rss(cap_bytes=1_500_000_000, stage="pipeline_execute")
        # No exception = pass


def test_check_worker_rss_disabled_when_cap_zero():
    """Cap <= 0 -> guard skipped, no exception even with high RSS."""
    from app.core.resources.memory_guard import check_worker_rss
    # Should not raise regardless of RSS
    check_worker_rss(cap_bytes=0, stage="pipeline_execute")
    check_worker_rss(cap_bytes=-1, stage="pipeline_execute")


def test_check_worker_rss_skips_when_psutil_unavailable():
    """If psutil is not available, guard is silently skipped."""
    from app.core.resources.memory_guard import check_worker_rss

    with patch("app.core.resources.memory_guard.get_current_rss_bytes", return_value=None):
        # Should not raise
        check_worker_rss(cap_bytes=1, stage="pipeline_execute")


def test_cleanup_worker_memory_runs_gc_and_malloc_trim_on_linux(monkeypatch):
    """Post-task cleanup should collect Python cycles and trim glibc arenas."""
    from app.core.resources import memory_guard

    class _FakeLibc:
        @staticmethod
        def malloc_trim(value):
            assert value == 0
            return 1

    monkeypatch.setattr(memory_guard.sys, "platform", "linux")
    monkeypatch.setattr(memory_guard.gc, "collect", lambda: 7)
    monkeypatch.setattr(memory_guard.ctypes, "CDLL", lambda name: _FakeLibc())

    with patch(
        "app.core.resources.memory_guard.get_current_rss_bytes",
        side_effect=[2_000_000_000, 1_100_000_000],
    ):
        stats = memory_guard.cleanup_worker_memory()

    assert stats == {
        "before_rss": 2_000_000_000,
        "after_rss": 1_100_000_000,
        "collected": 7,
        "malloc_trim": True,
    }


def test_cleanup_worker_memory_is_best_effort_when_malloc_trim_unavailable(monkeypatch):
    """malloc_trim failures must not change task outcome."""
    from app.core.resources import memory_guard

    monkeypatch.setattr(memory_guard.sys, "platform", "linux")
    monkeypatch.setattr(memory_guard.gc, "collect", lambda: 2)
    monkeypatch.setattr(
        memory_guard.ctypes,
        "CDLL",
        lambda name: (_ for _ in ()).throw(OSError("missing libc")),
    )

    with patch(
        "app.core.resources.memory_guard.get_current_rss_bytes",
        side_effect=[100, 90],
    ):
        stats = memory_guard.cleanup_worker_memory()

    assert stats["collected"] == 2
    assert stats["malloc_trim"] is False


# 3. Envelope shape: 503 for RSS stages

def test_rss_stage_maps_to_503_in_handler_source_bound():
    """RSS stages must map to 503 in `app/app_factory.py`, without importing
    the production app during this unit test.

    Importing `app.main` initializes DB/Redis startup boundaries; this
    source-bound contract keeps the test hermetic while still locking the
    status-code split introduced by this batch.
    """
    app_factory_py = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "app_factory.py"
    )
    src = app_factory_py.read_text(encoding="utf-8")
    assert '_RSS_STAGES = frozenset({"pipeline_execute", "worker_rss"})' in src
    assert "status_code = 503 if is_rss else 413" in src
    assert '"retry_later_or_contact_admin"' in src
    assert '"split_file_or_contact_admin"' in src


def test_rss_exception_carries_worker_process_resource():
    """MemoryLimitExceededError from RSS guard uses 'worker_process'
    as path_basename."""
    from app.core.resources.memory_guard import check_worker_rss

    with patch("app.core.resources.memory_guard.get_current_rss_bytes", return_value=2_000_000_000):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            check_worker_rss(cap_bytes=1, stage="pipeline_execute")

        assert exc_info.value.path_basename == "worker_process"


# 4. Config

def test_worker_rss_max_bytes_in_settings_defaults():
    """WORKER_RSS_MAX_BYTES must be present in Settings.DEFAULTS."""
    from app.config import Settings
    assert "WORKER_RSS_MAX_BYTES" in Settings.DEFAULTS
    assert isinstance(Settings.DEFAULTS["WORKER_RSS_MAX_BYTES"], int)
    assert Settings.DEFAULTS["WORKER_RSS_MAX_BYTES"] > 0
    assert Settings.DEFAULTS["WORKER_MEMORY_CLEANUP_ENABLED"] is True
    assert Settings.DEFAULTS["WORKER_MEMORY_MALLOC_TRIM_ENABLED"] is True
    assert Settings.DEFAULTS["WORKER_MEMORY_RECYCLE_ENABLED"] is True
    assert (
        Settings.DEFAULTS["WORKER_MEMORY_RECYCLE_RSS_BYTES"]
        == Settings.DEFAULTS["WORKER_RSS_MAX_BYTES"]
    )
