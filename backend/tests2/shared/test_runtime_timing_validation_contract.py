from __future__ import annotations

import pytest

from app.startup_validation import validate_runtime_timing_config


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


class _Settings(dict):
    def get(self, key, default=None):
        return super().get(key, default)


def test_default_runtime_timings_are_valid():
    errors = []
    validate_runtime_timing_config(_Settings(), errors)
    assert errors == []


def test_runtime_timing_rejects_lease_shorter_than_heartbeat_margin():
    errors = []
    validate_runtime_timing_config(
        _Settings({
            "WORKER_HEARTBEAT_INTERVAL_SECONDS": 20,
            "TASK_EXECUTION_LEASE_SECONDS": 30,
        }),
        errors,
    )
    assert any("TASK_EXECUTION_LEASE_SECONDS" in error for error in errors)


def test_runtime_timing_rejects_scheduler_heartbeat_too_close_to_ttl():
    errors = []
    validate_runtime_timing_config(
        _Settings({
            "SCHEDULE_LOCK_TTL_SECONDS": 100,
            "SCHEDULE_LOCK_HEARTBEAT_INTERVAL_SECONDS": 50,
        }),
        errors,
    )
    assert any("SCHEDULE_LOCK_TTL_SECONDS" in error for error in errors)


def test_runtime_timing_rejects_result_expiry_during_task():
    errors = []
    validate_runtime_timing_config(
        _Settings({"TASK_RESULT_EXPIRES": 3600, "TASK_HARD_LIMIT": 3900}),
        errors,
    )
    assert any("TASK_RESULT_EXPIRES" in error for error in errors)
