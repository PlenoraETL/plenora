"""Contract test — ogni `datetime.now()` nel codebase e' UTC aware.

Pattern AST-based (stessa filosofia di `test_csv_encoding_explicit_contract.py`):
catturiamo a CI-time qualsiasi PR che reintroduca `datetime.now()` o
`datetime.utcnow()` naive (deprecated dal 3.12) nei moduli di produzione.

Block:
  - `datetime.now()`           (no args, no `tz` kwarg) → naive local-time
  - `datetime.utcnow()`        (deprecated dal 3.12) → naive UTC

Allow:
  - `datetime.now(timezone.utc)`
  - `datetime.now(tz=timezone.utc)` (qualsiasi tz kwarg esplicito)
  - `time_provider.now()`, `self._time.now()`, ecc. (receiver != Name 'datetime')

Scope: `app/` + `workers/`. Test e scripts esclusi: i test possono
freezare timestamp in modi vari, gli script CLI tipicamente girano
una volta sola e il rischio drift non si materializza.

Motivazione: timestamp naive salvati in DB TIMESTAMPTZ vengono
interpretati da PostgreSQL come local time del server, generando bug
temporali invisibili (off-by-N-hours, ordering errato in catene
con failover cross-AZ, audit log con offset incoerenti).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator, Tuple

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parents[3]
SCANNED_DIRS = [BACKEND_ROOT / "app", BACKEND_ROOT / "workers"]


def _iter_naive_datetime_calls(tree: ast.AST) -> Iterator[Tuple[ast.Call, str]]:
    """Yield (call_node, reason) per ogni datetime.now()/utcnow() naive.

    Cattura solo il pattern `datetime.now()` / `datetime.utcnow()` dove
    `datetime` e' un Name (import classico `from datetime import datetime`).
    Method call su istanze custom (es. `self._time.now()`) non vengono
    flaggati: il receiver e' un Attribute, non un Name 'datetime'.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if func.value.id != "datetime":
            continue

        if func.attr == "utcnow":
            # Sempre naive (e deprecated dal 3.12)
            yield node, "datetime.utcnow() (deprecated, naive UTC)"
        elif func.attr == "now":
            # now() senza args e senza tz=... → naive local
            has_tz_kw = any(kw.arg == "tz" for kw in node.keywords)
            if not node.args and not has_tz_kw:
                yield node, "datetime.now() senza tz (naive local time)"


def test_no_naive_datetime_now_in_production_code():
    """Nessun datetime.now()/utcnow() naive in app/ + workers/."""
    offenders = []
    for root in SCANNED_DIRS:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            for call, reason in _iter_naive_datetime_calls(tree):
                rel = py_file.relative_to(BACKEND_ROOT)
                offenders.append(f"{rel}:{call.lineno}  {reason}")

    assert not offenders, (
        "Trovate chiamate datetime naive in produzione:\n  "
        + "\n  ".join(offenders)
        + "\n\nFix: sostituire con `datetime.now(timezone.utc)`. "
        + "I timestamp naive salvati in TIMESTAMPTZ vengono interpretati "
        + "come local-time del server → bug temporali invisibili."
    )


# Self-test del pattern AST: i casi accettati DEVONO essere riconosciuti
# come tali, altrimenti il guard genererebbe falsi positivi.


@pytest.mark.parametrize("code", [
    "from datetime import datetime, timezone\nx = datetime.now(timezone.utc)",
    "from datetime import datetime, timezone\nx = datetime.now(tz=timezone.utc)",
    "from datetime import datetime\nimport pytz\nx = datetime.now(tz=pytz.UTC)",
    # Method su istanza custom (NON e' datetime built-in)
    "class T:\n    def now(self): pass\nT().now()",
    # Stesso ma su attribute (es. self._time.now())
    "class C:\n    def f(self):\n        self._time.now()",
])
def test_ast_pattern_accepts_aware_calls(code):
    tree = ast.parse(code)
    offenders = list(_iter_naive_datetime_calls(tree))
    assert not offenders, f"Falso positivo su codice valido:\n{code}\n→ {offenders}"


@pytest.mark.parametrize("code,expected_reason_substring", [
    ("from datetime import datetime\ndatetime.now()", "naive local"),
    ("from datetime import datetime\ndatetime.utcnow()", "deprecated"),
])
def test_ast_pattern_catches_naive_calls(code, expected_reason_substring):
    tree = ast.parse(code)
    offenders = list(_iter_naive_datetime_calls(tree))
    assert len(offenders) == 1
    _, reason = offenders[0]
    assert expected_reason_substring in reason
