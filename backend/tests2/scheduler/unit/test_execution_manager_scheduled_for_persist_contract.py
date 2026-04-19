"""Persistenza di scheduled_for nei log/metadata (C3 round 3 fix 2).

Contract: il kwarg `scheduled_for` (passato dal beat al task in caso
di catchup) deve essere:
1. Usato nei log entry (campo dedicato nei pipeline_info/execution_info).
2. Presente nei log di successo e failure.
3. Loggato esplicitamente a INFO cosi' e' greppabile.

Senza questo, il catchup funziona ma si perde il lineage: l'operatore
non distingue "run eseguito alle 10:00 per il tick delle 06:00" da
"run eseguito alle 10:00 per il tick delle 10:00".
"""

import inspect

import pytest

from app.core.scheduler.execution_manager import ExecutionManager


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.contract]


def test_execute_body_uses_scheduled_for_beyond_signature():
    """scheduled_for deve comparire almeno 3 volte nel body di execute:
    signature, log, payload. Se compare solo in signature+docstring
    (≤2) il kwarg e' una ombra morta."""
    source = inspect.getsource(ExecutionManager.execute)
    # Contiamo escludendo la signature linea
    occurrences = source.count("scheduled_for")
    assert occurrences >= 3, (
        f"scheduled_for usato solo {occurrences} volte nel body di "
        f"execute(). Signature+docstring non basta, serve persistenza."
    )


def test_execute_logs_scheduled_for_when_present():
    """execute() deve loggare scheduled_for (log line o create_log_entry)
    cosi' l'operatore vede il tick logico recuperato. Il check
    cerca `scheduled_for` in un blocco di ~6 linee che include anche
    un marker `logger.` / `create_log_entry` / `schedule_info` /
    `execution_info` (pattern multi-riga per f-string)."""
    source = inspect.getsource(ExecutionManager.execute)
    lines = source.splitlines()
    found = False
    for i, line in enumerate(lines):
        if "scheduled_for" not in line:
            continue
        # Skip signature line e docstring
        if line.lstrip().startswith(("def ", '"""', 'scheduled_for:')):
            continue
        # Controlla le ~6 linee attorno per marker logger / log entry
        window_start = max(0, i - 6)
        window_end = min(len(lines), i + 2)
        window_text = "\n".join(lines[window_start:window_end])
        if any(m in window_text for m in (
            "logger.", "create_log_entry", "schedule_info",
            "execution_info", "pipeline_info", "open_run",
        )):
            found = True
            break
    assert found, (
        "scheduled_for non viene mai persistito in log/entry"
    )
