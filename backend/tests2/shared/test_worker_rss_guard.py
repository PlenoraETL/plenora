"""Contract test BE-GO-READY-MEMORY-OOM-002-WORKER-RSS-SAMPLING.

Tests:
1. Helper purity: `check_worker_rss` is pure (no FastAPI import).
2. Guard behavior: raises above cap, passes below, disabled when cap <= 0.
3. Envelope shape: 503 with memory_limit_exceeded code for RSS stages.
4. Config: WORKER_RSS_MAX_BYTES present in Settings.DEFAULTS.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.shared.exceptions import MemoryLimitExceededError
from app.shared import error_codes

pytestmark = [pytest.mark.unit, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Helper purity
# ---------------------------------------------------------------------------

def test_memory_guard_module_does_not_import_fastapi():
    """memory_guard.py must be pure — no FastAPI/starlette imports."""
    src = (Path(__file__).resolve().parent.parent.parent
           / "app" / "shared" / "memory_guard.py")
    text = src.read_text(encoding="utf-8")
    assert "from fastapi" not in text
    assert "from starlette" not in text
    assert "import fastapi" not in text


# ---------------------------------------------------------------------------
# 2. Guard behavior
# ---------------------------------------------------------------------------

def test_check_worker_rss_raises_when_above_cap():
    """RSS above cap -> MemoryLimitExceededError."""
    from app.shared.memory_guard import check_worker_rss

    with patch("app.shared.memory_guard.get_current_rss_bytes", return_value=2_000_000_000):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            check_worker_rss(cap_bytes=1_500_000_000, stage="pipeline_execute")

        assert exc_info.value.observed_bytes == 2_000_000_000
        assert exc_info.value.limit_bytes == 1_500_000_000
        assert exc_info.value.stage == "pipeline_execute"
        assert exc_info.value.path_basename == "worker_process"


def test_check_worker_rss_passes_when_below_cap():
    """RSS below cap -> no exception."""
    from app.shared.memory_guard import check_worker_rss

    with patch("app.shared.memory_guard.get_current_rss_bytes", return_value=500_000_000):
        check_worker_rss(cap_bytes=1_500_000_000, stage="pipeline_execute")
        # No exception = pass


def test_check_worker_rss_disabled_when_cap_zero():
    """Cap <= 0 -> guard skipped, no exception even with high RSS."""
    from app.shared.memory_guard import check_worker_rss
    # Should not raise regardless of RSS
    check_worker_rss(cap_bytes=0, stage="pipeline_execute")
    check_worker_rss(cap_bytes=-1, stage="pipeline_execute")


def test_check_worker_rss_skips_when_psutil_unavailable():
    """If psutil is not available, guard is silently skipped."""
    from app.shared.memory_guard import check_worker_rss

    with patch("app.shared.memory_guard.get_current_rss_bytes", return_value=None):
        # Should not raise
        check_worker_rss(cap_bytes=1, stage="pipeline_execute")


# ---------------------------------------------------------------------------
# 3. Envelope shape: 503 for RSS stages
# ---------------------------------------------------------------------------

def test_rss_stage_maps_to_503_in_handler_source_bound():
    """RSS stages must map to 503 in `app/main.py`, without importing
    the production app during this unit test.

    Importing `app.main` initializes DB/Redis startup boundaries; this
    source-bound contract keeps the test hermetic while still locking the
    status-code split introduced by this batch.
    """
    main_py = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    assert '_RSS_STAGES = frozenset({"pipeline_execute", "worker_rss"})' in src
    assert "status_code = 503 if is_rss else 413" in src
    assert 'suggested_action = "retry_later_or_contact_admin" if is_rss else "split_file_or_contact_admin"' in src


def test_rss_exception_carries_worker_process_resource():
    """MemoryLimitExceededError from RSS guard uses 'worker_process'
    as path_basename."""
    from app.shared.memory_guard import check_worker_rss

    with patch("app.shared.memory_guard.get_current_rss_bytes", return_value=2_000_000_000):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            check_worker_rss(cap_bytes=1, stage="pipeline_execute")

        assert exc_info.value.path_basename == "worker_process"


# ---------------------------------------------------------------------------
# 4. Config
# ---------------------------------------------------------------------------

def test_worker_rss_max_bytes_in_settings_defaults():
    """WORKER_RSS_MAX_BYTES must be present in Settings.DEFAULTS."""
    from app.config import Settings
    assert "WORKER_RSS_MAX_BYTES" in Settings.DEFAULTS
    assert isinstance(Settings.DEFAULTS["WORKER_RSS_MAX_BYTES"], int)
    assert Settings.DEFAULTS["WORKER_RSS_MAX_BYTES"] > 0
