"""Contract tests for Celery worker bootstrap import weight."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def test_worker_task_registration_does_not_import_concrete_arcgis_services() -> None:
    """Register Celery tasks without importing concrete provider/write services."""
    script = """
import json
import sys

from workers.celery_app import celery_app

celery_app.loader.import_default_modules()

blocked = [
    "app.core.features.features_service",
    "app.core.features.arcgis_feature_writer",
    "app.core.features.arcgis_client",
]
tasks = [
    "carica.execute",
    "pipeline.execute",
    "tasks.execute_enrichment",
    "workers.tasks.schedule_task.schedule_pipeline_task",
    "cleanroom.ping",
    "cleanroom.beat_ping",
]
print(json.dumps({
    "blocked_imported": [name for name in blocked if name in sys.modules],
    "missing_tasks": [name for name in tasks if name not in celery_app.tasks],
}, sort_keys=True))
"""
    env = {
        **os.environ,
        "DATABASE_URL": os.environ.get(
            "DATABASE_URL",
            "postgresql://test:test@127.0.0.1:5432/test",
        ),
        "PYTHONPATH": str(_BACKEND_ROOT),
    }

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_BACKEND_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["blocked_imported"] == []
    assert payload["missing_tasks"] == []
