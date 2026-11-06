"""Schedule celery-free per il beat.

Primitive proprie basate su ``croniter`` per valutare cron e tick del beat.

Timezone: i cron vengono interpretati nella timezone del beat (default
``Europe/Rome``, come faceva Celery beat con ``timezone="Europe/Rome"`` +
``enable_utc=True``) per preservare l'orario di sparo. Configurabile via
``BEAT_TIMEZONE``. ``last_run_at``/``now`` possono arrivare in qualunque
timezone aware: vengono normalizzati alla beat-tz prima del calcolo cron.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from croniter import croniter

_DEFAULT_BEAT_TZ = "Europe/Rome"


def beat_timezone() -> ZoneInfo:
    """ZoneInfo del beat (default Europe/Rome, override BEAT_TIMEZONE)."""
    try:
        from app.config import settings

        name = settings.get("BEAT_TIMEZONE", _DEFAULT_BEAT_TZ) or _DEFAULT_BEAT_TZ
    except Exception:
        name = _DEFAULT_BEAT_TZ
    try:
        return ZoneInfo(str(name))
    except Exception:
        return ZoneInfo(_DEFAULT_BEAT_TZ)


def beat_now() -> datetime:
    """Adesso, aware, nella timezone del beat (rimpiazzo di app.now())."""
    return datetime.now(beat_timezone())


def _as_aware(dt: datetime) -> datetime:
    """Rende il datetime aware (UTC se naive) — difensivo."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@runtime_checkable
class Schedule(Protocol):
    """Porta di scadenza: dato l'ultimo run e adesso, e' dovuto? + attesa."""

    def is_due(self, last_run_at: datetime, now: datetime) -> tuple[bool, float]:
        ...


class CronSchedule:
    """Schedule cron (5 campi) valutato con croniter nella beat-timezone."""

    def __init__(self, cron_expr: str) -> None:
        expr = " ".join(str(cron_expr).strip().split())
        if len(expr.split()) != 5 or not croniter.is_valid(expr):
            raise ValueError(f"cron non valido: {cron_expr!r}")
        self._expr = expr

    @property
    def expr(self) -> str:
        return self._expr

    def next_runs(self, after: datetime, count: int = 1) -> list[datetime]:
        """Prossimi ``count`` spari dopo ``after``, aware nella beat-timezone.

        Regola UNICA del calcolo: la usa ``is_due`` (quando sparare) e la
        fotografia ``frontend/tests/fixtures/cronRunsGolden.json`` con cui il
        wizard confronta le «prossime esecuzioni» che mostra (UI conferma C-K2).
        """
        it = croniter(self._expr, _as_aware(after).astimezone(beat_timezone()))
        return [it.get_next(datetime) for _ in range(max(0, int(count)))]

    def is_due(self, last_run_at: datetime, now: datetime) -> tuple[bool, float]:
        now_local = _as_aware(now).astimezone(beat_timezone())
        # Dovuto se il prossimo fire dopo l'ultimo run e' gia' passato.
        is_due = self.next_runs(last_run_at)[0] <= now_local
        # Attesa fino al prossimo fire dopo adesso.
        remaining = (self.next_runs(now_local)[0] - now_local).total_seconds()
        return is_due, max(0.0, remaining)

    def __str__(self) -> str:
        return f"cron({self._expr})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CronSchedule) and other._expr == self._expr

    def __hash__(self) -> int:
        return hash(("cron", self._expr))


class IntervalSchedule:
    """Schedule a intervallo fisso (secondi) — rimpiazzo di celery schedule()."""

    def __init__(self, run_every_seconds: float) -> None:
        self.run_every = float(run_every_seconds)

    def is_due(self, last_run_at: datetime, now: datetime) -> tuple[bool, float]:
        elapsed = (_as_aware(now) - _as_aware(last_run_at)).total_seconds()
        if elapsed >= self.run_every:
            return True, self.run_every
        return False, max(0.0, self.run_every - elapsed)

    def __str__(self) -> str:
        return f"every({self.run_every}s)"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, IntervalSchedule)
            and other.run_every == self.run_every
        )

    def __hash__(self) -> int:
        return hash(("interval", self.run_every))


@dataclass
class BeatEntry:
    """Entry schedulabile (rimpiazzo di celery.beat.ScheduleEntry).

    ``is_due()`` ritorna ``(dovuto, secondi_al_prossimo)``; ``next(entry)``
    ritorna una nuova entry con ``last_run_at`` ad adesso (avanza lo stato
    in-memory, come il ``next()`` di Celery)."""

    name: str
    task: str
    schedule: Any
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    options: dict = field(default_factory=dict)
    last_run_at: datetime = field(default_factory=beat_now)

    def is_due(self, now: Optional[datetime] = None) -> tuple[bool, float]:
        return self.schedule.is_due(self.last_run_at, now or beat_now())

    def __next__(self) -> "BeatEntry":
        return replace(self, last_run_at=beat_now())


__all__ = [
    "BeatEntry",
    "CronSchedule",
    "IntervalSchedule",
    "Schedule",
    "beat_now",
    "beat_timezone",
]
