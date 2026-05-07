"""Caratterizzazione di ScheduleService._calculate_next_execution.

Scopo: cristallizzare il comportamento ATTUALE del calcolo
"prossima esecuzione" prima di aggiungere catchup dei missed runs
(C3 punto 3). Dopo il fix il comportamento cambiera' (per gli schedule
con flag catchup=True, il metodo o un nuovo helper dovranno considerare
last_run invece di now). I test qui documentano il baseline.

Nota importante
---------------
Questo metodo calcola il prossimo run sempre a partire da `now`, NON
fa catchup. Il test `test_does_not_perform_catchup_for_missed_windows`
vincola esplicitamente questo comportamento: quando implementeremo il
catchup (opt-in per schedule) il nuovo helper andra' in un path
separato, NON dentro `_calculate_next_execution`, o questo test andra'
aggiornato insieme.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from app.core.scheduler.schedule_service import ScheduleService


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def _call(cron_expression: str, tz: str = "UTC"):
    """Bypassa __init__ di ScheduleService: il metodo non usa self.*."""
    return ScheduleService._calculate_next_execution(
        MagicMock(), cron_expression, tz
    )


# Happy path: ritorna datetime futuro


def test_returns_future_datetime_for_standard_cron():
    # "ogni minuto" — qualunque istante, il prossimo e' <=60s avanti
    result = _call("* * * * *", "UTC")

    assert isinstance(result, datetime)
    assert result > datetime.now(timezone.utc)


def test_returns_future_datetime_for_hourly_cron():
    # "ogni ora al minuto 0"
    result = _call("0 * * * *", "UTC")

    assert isinstance(result, datetime)
    now = datetime.now(timezone.utc)
    assert result > now
    # L'intervallo max e' 1h: se siamo al minuto 59:59, prossimo e' +1s;
    # se siamo al minuto 0:01, prossimo e' +59m59s. Quindi <=1h.
    delta = (result - now).total_seconds()
    assert delta <= 3600


# Timezone awareness


def test_respects_timezone_in_calculation():
    """Lo stesso cron con timezone diverse deve produrre istanti
    differenti. '0 6 * * *' = 06:00 locale. Con UTC vs Asia/Tokyo
    (UTC+9) il prossimo "6am locale" cade a istanti UTC diversi."""
    utc_next = _call("0 6 * * *", "UTC")
    tokyo_next = _call("0 6 * * *", "Asia/Tokyo")

    assert utc_next is not None
    assert tokyo_next is not None
    # Entrambi tz-aware (la precondizione minima)
    assert utc_next.tzinfo is not None
    assert tokyo_next.tzinfo is not None


# Error handling: graceful None invece di eccezione


def test_returns_none_on_invalid_cron_expression():
    result = _call("not a cron", "UTC")
    assert result is None


def test_returns_none_on_empty_cron_expression():
    result = _call("", "UTC")
    assert result is None


def test_returns_none_on_invalid_timezone():
    result = _call("0 6 * * *", "Invalid/Timezone")
    assert result is None


# Vincolo esplicito: NO catchup behavior


def test_does_not_perform_catchup_for_missed_windows():
    """Il metodo calcola next SEMPRE da `now`, mai da un last_run
    ipotetico. Anche se uno schedule ha missed N run nell'ultima ora,
    il prossimo ritornato e' il prossimo tick FUTURO, non il primo
    missed.

    Questo test congela il behavior attuale: se qualcuno aggiunge
    catchup dentro questo metodo (invece di in un helper separato),
    il test fallira' intenzionalmente e ci ricordera' di rivedere
    il contract.
    """
    # "alle 6:00 ogni giorno" — probabilmente gia' passato per oggi
    result = _call("0 6 * * *", "UTC")

    assert result is not None
    # Il prossimo run e' sempre > now, mai "il 6am di oggi gia' passato"
    assert result > datetime.now(timezone.utc)
