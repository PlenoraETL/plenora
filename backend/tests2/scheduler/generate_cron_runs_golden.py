"""Fotografa le prossime esecuzioni dello scheduler per ogni cron valido.

    python backend/tests2/scheduler/generate_cron_runs_golden.py

Per ogni caso valido di ``frontend/tests/fixtures/cronCases.json``, per ogni
istante di partenza e per ogni fuso del beat in ``ZONES``, le prossime
``COUNT`` esecuzioni calcolate con ``CronSchedule.next_runs`` (croniter, la
regola del beat), come ora locale ``YYYY-MM-DD HH:MM``. Il wizard degli
schedule (``frontend/src/utils/cron.nextCronRuns``) deve dare le stesse
(``frontend/tests/shared/cronWords.contract.test.js``);
``tests2/scheduler/unit/test_cron_runs_golden_contract.py`` verifica che la
fotografia sia ancora quella di croniter. Si rigenera solo quando cambiano i
casi o la regola del beat.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import sys
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from croniter import CroniterBadDateError  # noqa: E402

from app.core.scheduler import cron_schedule  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
CASES_PATH = REPO / "frontend" / "tests" / "fixtures" / "cronCases.json"
GOLDEN_PATH = REPO / "frontend" / "tests" / "fixtures" / "cronRunsGolden.json"

COUNT = 5
# Un venerdi' qualunque e i due fine settimana del cambio d'ora 2026/2027
# (Europa: 25/10/2026 e 28/03/2027; USA: 01/11/2026 e 14/03/2027).
STARTS = (
    "2026-11-06T09:17:30+00:00",
    "2026-10-24T12:00:00+00:00",
    "2027-03-27T12:00:00+00:00",
    "2026-10-31T12:00:00+00:00",
    "2027-03-13T12:00:00+00:00",
    # A ridosso del salto (Roma 02:00 -> 03:00 e 03:00 -> 02:00).
    "2027-03-28T00:50:00+00:00",
    "2026-10-25T00:50:00+00:00",
)
ZONES = ("Europe/Rome", "UTC", "America/New_York")


def runs_in(zone: str, expr: str, start: str) -> list[str]:
    from zoneinfo import ZoneInfo

    runs: list[datetime] = []
    after = datetime.fromisoformat(start)
    with mock.patch.object(cron_schedule, "beat_timezone", lambda: ZoneInfo(zone)):
        schedule = cron_schedule.CronSchedule(expr)
        while len(runs) < COUNT:
            try:
                after = schedule.next_runs(after)[0]
            except CroniterBadDateError:  # non capita (es. 30 febbraio): elenco vuoto
                break
            runs.append(after)
    return [r.strftime("%Y-%m-%d %H:%M") for r in runs]


def build() -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    valid = [c["expr"] for c in cases if c["valid"]]
    return {
        "_doc": (
            "Prossime esecuzioni dello scheduler (CronSchedule.next_runs, croniter) "
            "come ora locale del fuso del beat. Generato da "
            "backend/tests2/scheduler/generate_cron_runs_golden.py: non modificare a mano."
        ),
        "count": COUNT,
        "starts": list(STARTS),
        "zones": list(ZONES),
        "runs": {
            expr: {zone: {start: runs_in(zone, expr, start) for start in STARTS} for zone in ZONES}
            for expr in valid
        },
    }


if __name__ == "__main__":
    GOLDEN_PATH.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"scritto {GOLDEN_PATH}")
