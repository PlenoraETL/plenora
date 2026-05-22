"""Contract test for the RuntimeStatus enum.

Locks backend contracts and regression boundaries for the current architecture.

Refactor B3: ~9 callsite di runtime status erano string literal ("running",
"idle", "queued", "completed", "partial", "failed", "disabled", "orphan")
sparsi fra `schedule_service.py`, `execution_manager.py`,
`scheduler_execution_runtime.py`, `router.py`, `projects/service.py`. Sono
stati sostituiti con `RuntimeStatus(StrEnum)` per:
  1) prevenire typo silenti che atterravano nel DB
  2) avere single source-of-truth dei valori canonici
  3) preservare il row encoding DB (StrEnum.value e' una plain str)

Questo test pinna:
  - L'enum espone i 8 valori canonici come `.value` stringa
  - `set_runtime_status` accetta sia enum che str (back-compat con
    callsite legacy non ancora migrati)
  - NON ci sono residual literal `"running"`/`"idle"`/`"queued"` nei
    file scheduler refactorati (assertion architetturale grep-style)
  - L'allineamento fra `RuntimeStatus` enum e `ScheduleStatus` Literal
    rimane (assert nel modulo `models.py`)
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.scheduler.models import ScheduleStatus
from app.core.scheduler.runtime_status import RuntimeStatus, coerce_runtime_status


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


# Valori canonici attesi del runtime status. Aggiornare QUESTA lista (e
# l'enum) SOLO con un PR esplicito; non e' un test da "auto-aggiornare"
# se fallisce: il fallimento indica che qualcuno ha aggiunto/rimosso uno
# stato senza coordinare il rename cross-modulo.
_EXPECTED_VALUES = {
    "idle",
    "running",
    "queued",
    "completed",
    "partial",
    "failed",
    "disabled",
    "orphan",
}


def test_runtime_status_enum_exposes_canonical_string_values():
    """Ogni member dell'enum ha `.value` stringa equivalente al nome
    lowercase, e l'insieme coincide con i valori canonici attesi.
    """
    actual = {s.value for s in RuntimeStatus}
    assert actual == _EXPECTED_VALUES, (
        f"RuntimeStatus drift: atteso {_EXPECTED_VALUES}, trovato {actual}"
    )
    for s in RuntimeStatus:
        assert isinstance(s.value, str)
        # Pre-fix: senza questo check un member tipo `RUNNING = 1` sarebbe
        # passato silenziosamente, rompendo il row encoding DB.
        assert isinstance(s, str), (
            f"{s.name}: StrEnum members devono essere subclasse di str"
        )


def test_runtime_status_aligned_with_pydantic_literal():
    """Il `ScheduleStatus = Literal[...]` di models.py e' l'interfaccia
    visibile a Pydantic / JSON schema FE. Deve coincidere coi valori
    enum, altrimenti si rompe la validazione di config dump/load.
    """
    literal_args = set(ScheduleStatus.__args__)
    enum_values = {s.value for s in RuntimeStatus}
    assert literal_args == enum_values


def test_coerce_runtime_status_accepts_enum_and_str():
    """Back-compat boundary: caller possono passare enum o stringa
    legacy. La normalizzazione produce stringa "pura" per il DB.
    """
    # Enum -> string canonica
    assert coerce_runtime_status(RuntimeStatus.RUNNING) == "running"
    assert coerce_runtime_status(RuntimeStatus.IDLE) == "idle"
    # String legacy -> invariata (caller non ancora migrato)
    assert coerce_runtime_status("queued") == "queued"
    assert coerce_runtime_status("failed") == "failed"


def test_set_runtime_status_signature_accepts_both_enum_and_str():
    """Architectural: la signature di `ScheduleService.set_runtime_status`
    deve accettare sia enum che str. Verifica via runtime behavior con
    mock del row update.
    """
    from unittest.mock import patch

    from app.core.scheduler.schedule_service import ScheduleService

    service = ScheduleService()
    # Mock di `_update_config_locked` per isolare il path argument-handling
    # dal DB. La signature accetta gia' enum (post-fix): verifichiamo che
    # la chiamata col tipo enum non lanci `TypeError`.
    with patch.object(service, "_update_config_locked", return_value=None) as m:
        # Enum input
        service.set_runtime_status("sched-x", RuntimeStatus.RUNNING)
        # String input (back-compat)
        service.set_runtime_status("sched-x", "idle")

    assert m.call_count == 2


# Architectural grep assertion: nei file core dello scheduler refactorati
# i runtime status non devono piu' essere literal string. Il check usa
# pattern restrittivi per evitare falsi positivi su:
#   - LogStep(status="success",...) — enum logica step pipeline, diverso
#   - close_run(status="success"/"failure",...) — logger interface
#   - return {"status": "queued"} — payload JSON al FE
#   - _set_nx(key, "running",...) — testo marker DB lock-store
# Il regex matcha SOLO le forme:
#   - `set_runtime_status(..., "<status>")`
#   - `set_runtime_status(..., status="<status>")`
#   - `update_runtime(..., status="<status>",...)`
#   - `config.runtime.status = "<status>"`
#   - `runtime.status == "<status>"` / `!=`

_BACKEND_ROOT = Path(__file__).resolve().parents[3] / "app" / "core" / "scheduler"

_REFACTORED_FILES = [
    _BACKEND_ROOT / "schedule_service.py",
    _BACKEND_ROOT / "execution_manager.py",
    _BACKEND_ROOT / "scheduler_execution_runtime.py",
    _BACKEND_ROOT / "router.py",
]

_FORBIDDEN_PATTERNS = [
    # `set_runtime_status(..., "<status>")` positional o keyword
    re.compile(
        r"set_runtime_status\s*\([^)]*?(?:status\s*=\s*)?[\"']"
        r"(idle|running|queued|completed|partial|failed|disabled|orphan)"
        r"[\"']"
    ),
    # `config.runtime.status = "<status>"` (assignment)
    re.compile(
        r"runtime\.status\s*=\s*[\"']"
        r"(idle|running|queued|completed|partial|failed|disabled|orphan)"
        r"[\"']"
    ),
    # `runtime.status == "<status>"` / `!=` (comparison)
    re.compile(
        r"runtime\.status\s*[!=]=\s*[\"']"
        r"(idle|running|queued|completed|partial|failed|disabled|orphan)"
        r"[\"']"
    ),
]


def _strip_comments_and_docstrings(src: str) -> str:
    """Rimuove commenti `#...` e triple-quoted blocks per non far
    fallire il grep su pre-fix/post-fix notes che riferiscono i
    literal storici a scopo documentativo.
    """
    # Rimuovi triple-quoted strings (doc + commenti multi-line)
    src = re.sub(r'"""[\s\S]*?"""', "", src)
    src = re.sub(r"'''[\s\S]*?'''", "", src)
    # Rimuovi linea-comment
    src = re.sub(r"(?m)#.*$", "", src)
    return src


@pytest.mark.parametrize("path", _REFACTORED_FILES, ids=lambda p: p.name)
def test_no_residual_runtime_status_string_literals(path: Path):
    """Assert architettura: i file refactorati non devono contenere
    pattern di assegnazione/comparison di runtime.status verso literal
    stringa. SE questo fallisce, qualcuno ha re-introdotto un literal
    bypassando l'enum.
    """
    assert path.is_file(), f"File scheduler atteso non trovato: {path}"
    src = path.read_text(encoding="utf-8")
    src_clean = _strip_comments_and_docstrings(src)

    violations: list[tuple[str, str]] = []
    for pat in _FORBIDDEN_PATTERNS:
        for m in pat.finditer(src_clean):
            # Recupera la linea originale per error message utile
            start = src_clean.rfind("\n", 0, m.start()) + 1
            end = src_clean.find("\n", m.end())
            if end == -1:
                end = len(src_clean)
            violations.append((m.group(1), src_clean[start:end].strip()))

    assert not violations, (
        f"{path.name}: trovati {len(violations)} runtime status literal "
        f"residui (devono usare `RuntimeStatus.*`):\n"
        + "\n".join(f"  - {status!r}: {line}" for status, line in violations)
    )
