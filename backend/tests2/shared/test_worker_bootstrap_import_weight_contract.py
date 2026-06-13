"""Contract: import weight del bootstrap worker Dramatiq.

Plenora 2.0 Fase 5: importare ``workers.dramatiq_app`` registra tutti gli
actor (con i nomi storici dei task) SENZA tirare dentro i service
provider/write concreti — i corpi dei task fanno import lazy dei moduli
applicativi solo quando l'actor viene eseguito. Tiene leggero il boot del
worker e blinda l'assenza di import pesanti a registration-time.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def test_actor_registration_does_not_import_concrete_arcgis_services() -> None:
    """Registra gli actor Dramatiq senza importare provider/write concreti."""
    script = """
import json
import sys

import workers.dramatiq_app as dramatiq_app

registered = {
    getattr(dramatiq_app, attr).actor_name
    for attr in dir(dramatiq_app)
    if hasattr(getattr(dramatiq_app, attr), "actor_name")
}

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
    "missing_tasks": [name for name in tasks if name not in registered],
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
