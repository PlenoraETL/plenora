"""Contract tests for explicit Celery control/data-plane queue routing."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_CELERY_APP = _REPO_ROOT / "backend" / "workers" / "celery_app.py"
_CONFIG = _REPO_ROOT / "backend" / "app" / "config.py"


def test_celery_settings_expose_control_data_scheduler_queues():
    config_text = _CONFIG.read_text(encoding="utf-8")

    for setting_name in (
        "CELERY_QUEUE_DEFAULT",
        "CELERY_QUEUE_DATA_PLANE",
        "CELERY_QUEUE_CONTROL_PLANE",
        "CELERY_QUEUE_SCHEDULER",
    ):
        assert setting_name in config_text


def test_celery_app_routes_heavy_data_tasks_to_data_plane_queue():
    celery_text = _CELERY_APP.read_text(encoding="utf-8")

    for task_name in (
        '"pipeline.execute"',
        '"pipeline.preview"',
        '"carica.execute"',
        '"tasks.execute_enrichment"',
        '"workers.tasks.schedule_task.schedule_pipeline_task"',
    ):
        assert task_name in celery_text
    assert "settings.CELERY_QUEUE_DATA_PLANE" in celery_text


def test_celery_app_routes_ops_and_heartbeat_tasks_away_from_data_plane():
    celery_text = _CELERY_APP.read_text(encoding="utf-8")

    assert '"workers.tasks.admin_backup_task.run_manual_backup_task"' in celery_text
    assert "settings.CELERY_QUEUE_CONTROL_PLANE" in celery_text
    assert '"workers.tasks.schedule_task.beat_heartbeat"' in celery_text
    assert "settings.CELERY_QUEUE_SCHEDULER" in celery_text
