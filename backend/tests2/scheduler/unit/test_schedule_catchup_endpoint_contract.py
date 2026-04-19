"""Endpoint PATCH /api/schedules/{id}/catchup (C3 fix 3).

Permette all'admin/owner di abilitare o disabilitare il catchup su uno
schedule esistente, e di configurare la grace window, senza dover
toccare l'intero ScheduleConfig via update generico. Endpoint dedicato
per pulizia API (stesso pattern di /{id}/toggle).
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.scheduler.router import router as schedules_router
from app.core.auth.auth import require_schedule_owner
from tests2.helpers.builders import build_current_user


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(schedules_router, prefix="/api")
    app.dependency_overrides[require_schedule_owner] = lambda: build_current_user()
    return TestClient(app)


def test_patch_catchup_enables_flag_and_sets_grace_hours(client, schedule_config):
    """Happy path: PATCH con enabled + grace_hours aggiorna il service."""
    schedule_config.schedule.catchup_missed_runs = True
    schedule_config.schedule.catchup_grace_hours = 6

    fake_service = MagicMock()
    fake_service.update_catchup_config.return_value = schedule_config

    with patch("app.core.scheduler.schedule_service.ScheduleService", return_value=fake_service):
        resp = client.patch(
            f"/api/schedules/{schedule_config.id}/catchup",
            json={"enabled": True, "grace_hours": 6},
        )

    assert resp.status_code == 200
    fake_service.update_catchup_config.assert_called_once_with(
        schedule_config.id, enabled=True, grace_hours=6,
    )


def test_patch_catchup_rejects_grace_hours_zero(client):
    """grace_hours=0 deve essere rifiutato a livello di validazione body."""
    resp = client.patch(
        "/api/schedules/sched-1/catchup",
        json={"enabled": True, "grace_hours": 0},
    )
    assert resp.status_code == 422


def test_patch_catchup_rejects_grace_hours_negative(client):
    resp = client.patch(
        "/api/schedules/sched-1/catchup",
        json={"enabled": True, "grace_hours": -5},
    )
    assert resp.status_code == 422


def test_patch_catchup_returns_404_for_unknown_schedule(client):
    """Se lo schedule non esiste il service ritorna None → 404."""
    fake_service = MagicMock()
    fake_service.update_catchup_config.return_value = None

    with patch("app.core.scheduler.schedule_service.ScheduleService", return_value=fake_service):
        resp = client.patch(
            "/api/schedules/does-not-exist/catchup",
            json={"enabled": False, "grace_hours": 24},
        )

    assert resp.status_code == 404
