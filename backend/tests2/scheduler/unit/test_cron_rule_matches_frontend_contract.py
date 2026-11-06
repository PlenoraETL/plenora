"""La regola del cron del frontend e' quella dello scheduler (Fase 5 T5, R8.13).

Il wizard degli schedule valida il cron in ``frontend/src/utils/cron.js``;
lo scheduler lo accetta solo con 5 campi e ``croniter.is_valid``
(``app.core.scheduler.cron_schedule.CronSchedule``). La tabella dei casi e'
una sola, ``frontend/tests/fixtures/cronCases.json``: il frontend la verifica
con la sua funzione, questo test con lo scheduler vero.

- ``valid``: lo scheduler accetta se e solo se il caso dice ``true``;
- ``warning`` (conferma finale G6, al posto di ``stricter``): lo scheduler
  accetta una forma che esegue diversamente da come si legge; il frontend la
  accetta lo stesso (una pianificazione salvata si deve poter risalvare) e
  avvisa. Nessun caso in cui frontend e scheduler non sono d'accordo.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.scheduler.cron_schedule import CronSchedule

_CASES = Path(__file__).resolve().parents[4] / "frontend" / "tests" / "fixtures" / "cronCases.json"

pytestmark = pytest.mark.skipif(not _CASES.exists(), reason="frontend non presente in questo ambiente")


def _scheduler_accepts(expr: str) -> bool:
    try:
        CronSchedule(expr)
    except ValueError:
        return False
    return True


def _cases() -> list[dict]:
    return json.loads(_CASES.read_text(encoding="utf-8"))["cases"]


def test_cases_table_is_not_empty() -> None:
    cases = _cases()
    assert len(cases) > 50
    assert any(c["valid"] for c in cases) and any(not c["valid"] for c in cases)


@pytest.mark.parametrize("case", _cases() if _CASES.exists() else [], ids=lambda c: repr(c["expr"]))
def test_scheduler_agrees_with_frontend_table(case: dict) -> None:
    accepted = _scheduler_accepts(case["expr"])
    # Conferma finale G6: il frontend accetta tutto cio' che lo scheduler
    # accetta; le forme lette diversamente hanno solo un avviso (`warning`).
    assert "stricter" not in case
    if case.get("warning"):
        assert accepted and case["valid"] is True
    assert accepted is case["valid"]
