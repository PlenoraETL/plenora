"""
Orari verso il client sempre in UTC esplicito (Fase 5 T7, correzione).

Regola (app/shared/utc_time): un istante formattato SENZA fuso
(``strftime`` senza ``%z``/``%Z``) è in UTC. Un TIMESTAMPTZ letto da PG è
nel fuso della sessione: formattarlo così senza conversione sposta l'ora
(frontend: utils/format.parseServerDate legge in UTC gli orari senza fuso).
``isoformat()`` di un datetime aware porta il fuso ed è sempre leggibile.

Enumerazione su app/ e workers/:
  1. ogni ``<x>.strftime(fmt)`` senza fuso ha come oggetto un valore già
     UTC (``datetime.now(timezone.utc)``, un nome che dice ``utc``,
     ``as_utc(...)``) oppure è un'eccezione dichiarata col motivo;
  2. nessun datetime naive LOCALE (``datetime.now()``, ``utcnow()``,
     ``today()``, ``fromtimestamp(x)`` senza tz) fuori dalle eccezioni:
     così «naive = UTC» resta vero e ``as_utc`` lo può assumere.
Una dichiarazione superata fa fallire il test.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND = Path(__file__).resolve().parent.parent.parent
_ROOTS = (_BACKEND / "app", _BACKEND / "workers")

# (file, oggetto del strftime) -> motivo.
_STRFTIME_OK = {
    ("app/core/files/service.py", "self._time.now()"):
        "id del file, non un orario mostrato; DefaultTimeProvider.now è UTC",
    ("app/core/projects/domain.py", "generated_at"):
        "id leggibile del progetto, non un orario mostrato",
    ("app/core/scheduler/log_service.py", "timestamp"):
        "log_id (sched_<id>_HHMMSS_ffffff), non un orario mostrato; timestamp = now UTC",
    ("app/utils/template_variables.py", "dt"):
        "variabili {{NOW}}/{{TODAY}} dei template verso API esterne: formato scelto dall'utente",
    ("app/utils/logger.py", "datetime.fromtimestamp(record.created)"):
        "riga del file di log locale (ora della macchina), non una risposta HTTP",
}
# File -> motivo, per i datetime naive locali.
_NAIVE_OK = {
    "app/utils/logger.py": "file di log locale: ora della macchina",
}

_UTC_RECEIVER = re.compile(r"datetime\.now\(\s*(?:tz\s*=\s*)?timezone\.utc\s*\)|utc|^as_utc\(", re.IGNORECASE)


def _files():
    for root in _ROOTS:
        for p in root.rglob("*.py"):
            if "__pycache__" in p.parts or "tests" in p.parts:
                continue
            yield p, str(p.relative_to(_BACKEND)).replace("\\", "/")


def _scan():
    strftimes: list[tuple[str, str, int]] = []
    naive: list[tuple[str, int, str]] = []
    for path, rel in _files():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            attr = node.func.attr
            receiver = ast.get_source_segment(src, node.func.value) or ""
            if attr == "strftime":
                fmt = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
                if isinstance(fmt, str) and re.search(r"%[zZ]", fmt):
                    continue
                strftimes.append((rel, " ".join(receiver.split()), node.lineno))
            elif receiver == "datetime":
                has_tz = any(k.arg == "tz" for k in node.keywords)
                if attr in ("utcnow", "today") \
                        or (attr == "now" and not node.args and not has_tz) \
                        or (attr == "fromtimestamp" and len(node.args) < 2 and not has_tz):
                    naive.append((rel, node.lineno, f"datetime.{attr}"))
    return strftimes, naive


_STRFTIMES, _NAIVE = _scan()


def test_scan_finds_strftime_calls():
    assert len(_STRFTIMES) > 10
    assert any(r == "app/core/scheduler/log_archive.py" for r, _, _ in _STRFTIMES)


def test_strftime_without_zone_only_on_utc_values():
    bad = [
        f"{rel}:{line} {receiver}.strftime(...)"
        for rel, receiver, line in _STRFTIMES
        if not _UTC_RECEIVER.search(receiver) and (rel, receiver) not in _STRFTIME_OK
    ]
    assert bad == [], (
        "strftime senza fuso su un datetime non UTC: usare app.shared.utc_time "
        "(utc_stamp / as_utc) o dichiarare l'eccezione col motivo"
    )


def test_no_stale_strftime_exceptions():
    seen = {(rel, receiver) for rel, receiver, _ in _STRFTIMES}
    assert [k for k in _STRFTIME_OK if k not in seen] == []


def test_no_naive_local_datetimes():
    bad = [f"{rel}:{line} {call}()" for rel, line, call in _NAIVE if rel not in _NAIVE_OK]
    assert bad == []
    assert [f for f in _NAIVE_OK if f not in {rel for rel, _, _ in _NAIVE}] == []


def test_schedule_run_tokens_go_through_utc_stamp():
    for rel in ("app/core/scheduler/db.py", "app/core/scheduler/runs_catalog.py"):
        src = (_BACKEND / rel).read_text(encoding="utf-8")
        assert "utc_stamp(" in src, rel
        assert '"%Y%m%d_%H%M%S"' not in src, rel
