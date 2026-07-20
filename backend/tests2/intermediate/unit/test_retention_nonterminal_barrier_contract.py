from __future__ import annotations

import pytest

from app.core.intermediate.retention import apply_runs_retention


pytestmark = [pytest.mark.unit, pytest.mark.intermediate, pytest.mark.contract]


def test_retention_never_deletes_running_or_uncertain_barriers():
    runs = [
        {"run_id": "completed-new", "status": "completed", "pinned": False},
        {"run_id": "running-old", "status": "running", "pinned": False},
        {
            "run_id": "uncertain-old",
            "status": "completion_uncertain",
            "pinned": False,
        },
        {"run_id": "failed-old", "status": "failed", "pinned": False},
    ]
    deleted = []

    apply_runs_retention(
        "project-1",
        "tenant-1",
        retention_n=1,
        list_runs_func=lambda *_a: runs,
        delete_run_func=deleted.append,
    )

    assert deleted == ["failed-old"]


def test_retention_keeps_unknown_future_status_fail_closed():
    runs = [
        {"run_id": "completed-new", "status": "completed", "pinned": False},
        {"run_id": "future-state", "status": "awaiting_review", "pinned": False},
        {"run_id": "completed-old", "status": "completed", "pinned": False},
    ]
    deleted = []

    apply_runs_retention(
        "project-1",
        "tenant-1",
        retention_n=1,
        list_runs_func=lambda *_a: runs,
        delete_run_func=deleted.append,
    )

    assert deleted == ["completed-old"]
