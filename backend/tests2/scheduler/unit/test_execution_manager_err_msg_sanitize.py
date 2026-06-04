"""
Contract: execution manager surfaces canonical safe error messages.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import all_identifiers, uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_EXECUTION_MANAGER = _BACKEND_ROOT / "app/core/scheduler/execution_manager.py"
_RUNTIME = _BACKEND_ROOT / "app/core/scheduler/scheduler_execution_runtime.py"


def _read_manager_src() -> str:
    return _EXECUTION_MANAGER.read_text(encoding="utf-8")


def _read_runtime_src() -> str:
    return _RUNTIME.read_text(encoding="utf-8")




def test_err_msg_inatteso_no_str_e_interpolation():
    """Il sorgente NON deve piu' contenere `err_msg = f"Errore inatteso: {e}"`
    (pattern Previous behavior che interpolava str(e) user-facing). Current contract la
    stringa italiana canonica fissa e' presente."""
    src = _read_manager_src()

    # Pattern Previous behavior rimosso
    assert 'err_msg = f"Errore inatteso: {e}"' not in src, (
        "pattern Previous behavior `err_msg = f\"Errore inatteso: {e}\"` ancora presente"
    )
    # Stringa canonica Current contract presente
    assert (
        'err_msg = "Errore inatteso durante l\'esecuzione dello schedule '
        '(consulta log)"'
    ) in src




def test_err_msg_execution_error_branch_preserved():
    """Il ramo `isinstance(e, ExecutionError): err_msg = str(e)` deve restare
    invariato â€” ExecutionError e' domain exception costruita dal codice
    applicativo (message controllato), NON leak di internals."""
    src = _read_manager_src()
    # Pattern ExecutionError branch preservato
    assert "if isinstance(e, ExecutionError):" in src
    # `err_msg = str(e)` presente sotto quel branch (domain safe)
    # Verifica che il pattern `isinstance(e, ExecutionError)` sia seguito
    # da `err_msg = str(e)` a breve distanza (entro 3 righe).
    lines = src.splitlines()
    exec_error_line = None
    for i, line in enumerate(lines):
        if "isinstance(e, ExecutionError)" in line:
            exec_error_line = i
            break
    assert exec_error_line is not None, (
        "branch `isinstance(e, ExecutionError)` non trovato"
    )
    # Cerca `err_msg = str(e)` nelle 3 righe successive
    window = "\n".join(lines[exec_error_line:exec_error_line + 4])
    assert "err_msg = str(e)" in window, (
        f"`err_msg = str(e)` atteso nel branch ExecutionError, ricevuto: {window}"
    )




def test_handle_failure_failed_branch_has_canonical_error_token():
    """Il return dict del branch `failed` in `handle_failure` deve contenere
    `"error": "schedule_execution_fatal"` (token machine-readable coerente
    con pattern 7.c). Il branch `retry` resta invariato (decisione ratificata:
    niente token retry in questo batch)."""
    manager_src = _read_manager_src()
    src = _read_runtime_src()

    # RAW: stringa-literal dict key/value, non identificatore
    assert '"error": "schedule_execution_fatal"' not in manager_src, (
        "failure finalization must stay in scheduler_execution_runtime.py; "
        "ExecutionManager should call handle_failure directly"
    )
    assert "decide_after_failure" not in all_identifiers(manager_src)
    assert "DebugPayloadBuilder" not in all_identifiers(manager_src)
    assert not uses_attribute(manager_src, "schedule_pipeline_task", "apply_async")
    assert "decide_after_failure" in all_identifiers(src)
    assert "DebugPayloadBuilder" in all_identifiers(src)
    assert not uses_attribute(src, "schedule_pipeline_task", "apply_async")
    assert "enqueue_schedule_run" in all_identifiers(src)
    # Token fatal presente
    assert '"error": "schedule_execution_fatal"' in src, (
        "token canonico `schedule_execution_fatal` non trovato nel file"
    )
    # Verifica contestuale: il token deve apparire in un return dict che
    # contiene anche `"status": "failed"` (failed branch), non retry.
    # Cerca pattern `"status": "failed",\n...\n"error": "schedule_execution_fatal"`
    # o ordine simile dentro lo stesso return dict.
    lines = src.splitlines()
    token_line = None
    for i, line in enumerate(lines):
        if '"error": "schedule_execution_fatal"' in line:
            token_line = i
            break
    assert token_line is not None
    # Scan backward fino a trovare il `return {` di questo dict
    return_line = None
    for j in range(token_line, max(token_line - 10, 0), -1):
        if "return {" in lines[j]:
            return_line = j
            break
    assert return_line is not None, (
        "return { non trovato prima del token schedule_execution_fatal"
    )
    # Finestra: le righe tra return { e la riga del token devono contenere
    # `"status": "failed"` (non retry).
    window = "\n".join(lines[return_line:token_line + 1])
    assert '"status": "failed"' in window, (
        f"token schedule_execution_fatal deve essere nel failed branch, "
        f"ricevuto: {window}"
    )
    # Negativo: NON deve essere nel retry branch
    assert '"status": "retry"' not in window


def test_no_schedule_execution_retry_token_added():
    """Decisione ratificata: niente token `schedule_execution_retry` nel
    branch retry in questo batch. Verifica che non sia stato aggiunto
    per errore."""
    combined = _read_manager_src() + "\n" + _read_runtime_src()
    assert "schedule_execution_retry" not in combined, (
        "token schedule_execution_retry NON doveva essere aggiunto in 7.c-bis"
    )
