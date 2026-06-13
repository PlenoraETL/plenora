"""Guard finale Plenora 2.0 Fase 5: zero import di celery/redis nel codice
di produzione (app/ + workers/).

Il cutover dello stack e' completo: il broker dei task e' RabbitMQ
(Dramatiq), stato/progress/result, lock, JWT blacklist e rate-limit sono su
PostgreSQL, il beat e' APScheduler + croniter. Nessun modulo di app/ o
workers/ deve piu' importare ``celery`` o ``redis``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_SCANNED_ROOTS = ("app", "workers")

# Match solo statement di import del modulo celery/redis (non commenti,
# stringhe, o nomi di setting come CELERY_QUEUE_*). Il modulo importato deve
# essere esattamente ``celery`` o ``redis`` (eventuale sottomodulo via punto).
_IMPORT_RE = re.compile(r"\s*(?:import|from)\s+(celery|redis)(?:\.|\s|$)")


def _offending_lines() -> list[str]:
    offenders: list[str] = []
    for root_name in _SCANNED_ROOTS:
        root = _BACKEND_ROOT / root_name
        for py_file in root.rglob("*.py"):
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(source.splitlines(), 1):
                if _IMPORT_RE.match(line):
                    rel = py_file.relative_to(_BACKEND_ROOT).as_posix()
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    return offenders


def test_no_celery_or_redis_imports_in_production_code():
    offenders = _offending_lines()
    assert offenders == [], (
        "Plenora 2.0 Fase 5: nessun modulo di produzione (app/ + workers/) "
        "deve importare celery o redis (broker su RabbitMQ/Dramatiq, stato e "
        "lock su PostgreSQL). Trovati:\n  " + "\n  ".join(offenders)
    )
