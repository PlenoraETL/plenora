"""
Campi catchup sulla ScheduleFrequency (C3 fix 3).

Aggiunti:
"""

import pytest
from pydantic import ValidationError

from app.core.scheduler.models import ScheduleFrequency


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_default_catchup_is_disabled():
    """Retrocompat: schedule esistenti senza catchup field restano off."""
    freq = ScheduleFrequency(cron_expression="0 6 * * *")
    assert freq.catchup_missed_runs is False
    assert freq.catchup_grace_hours == 24


def test_catchup_can_be_enabled_via_kwarg():
    freq = ScheduleFrequency(
        cron_expression="0 6 * * *",
        catchup_missed_runs=True,
        catchup_grace_hours=6,
    )
    assert freq.catchup_missed_runs is True
    assert freq.catchup_grace_hours == 6


def test_catchup_grace_hours_must_be_positive():
    """grace_hours <= 0 non ha senso (disabiliterebbe de facto) ma
    accettare valori come 0 sarebbe confusing: rifiutiamo con ValidationError."""
    with pytest.raises(ValidationError):
        ScheduleFrequency(
            cron_expression="0 6 * * *",
            catchup_missed_runs=True,
            catchup_grace_hours=0,
        )


def test_catchup_grace_hours_rejects_negative():
    with pytest.raises(ValidationError):
        ScheduleFrequency(
            cron_expression="0 6 * * *",
            catchup_missed_runs=True,
            catchup_grace_hours=-1,
        )
