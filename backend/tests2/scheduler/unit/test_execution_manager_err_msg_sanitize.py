"""[phase-2-err][batch7] 7.c-bis — sanitize err_msg in execution_manager.

Audit Batch 7 §3.1 S1 — leak `str(e)` via flow indiretto `err_msg →
_handle_failure → return dict Celery`. Il ramo `except Exception` broad
al sito 638 costruiva `err_msg = f"Errore inatteso: {e}"` che finiva
user-facing tramite `_handle_failure` return `{..., "message":
error_message, ...}` linea 1107.

Post-fix (7.c-bis — variante B ratificata):
  - Sito 638: `err_msg` sanitizzato a stringa italiana fissa
    "Errore inatteso durante l'esecuzione dello schedule (consulta log)".
  - Ramo `isinstance(e, ExecutionError)` INVARIATO (domain exception safe).
  - `_handle_failure` failed branch: aggiunto token canonico
    `"error": "schedule_execution_fatal"`. Retry branch NON toccato
    (decisione ratificata: niente token retry in questo batch).
  - Canali admin preservati: logger.error [CRASH] + log_service +
    close_run continuano a ricevere err_msg tecnico per postmortem.

Approccio test source-based (come 7.c): i mock runtime di
ExecutionManager.execute sono complessi (Celery + DB + threading).
Scansioniamo il codice sorgente del file modificato.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_TARGET = _BACKEND_ROOT / "app/core/scheduler/execution_manager.py"


def _read_src() -> str:
    return _TARGET.read_text(encoding="utf-8")


# =============================================================================
# TEST 1 — err_msg ramo broad Exception sanitizzato (no str(e) interpolato)
# =============================================================================


def test_err_msg_inatteso_no_str_e_interpolation():
    """Il sorgente NON deve piu' contenere `err_msg = f"Errore inatteso: {e}"`
    (pattern pre-fix che interpolava str(e) user-facing). Post-fix la
    stringa italiana canonica fissa e' presente."""
    src = _read_src()

    # Pattern pre-fix rimosso
    assert 'err_msg = f"Errore inatteso: {e}"' not in src, (
        "pattern pre-fix `err_msg = f\"Errore inatteso: {e}\"` ancora presente"
    )
    # Stringa canonica post-fix presente
    assert (
        'err_msg = "Errore inatteso durante l\'esecuzione dello schedule '
        '(consulta log)"'
    ) in src


# =============================================================================
# TEST 2 — Ramo ExecutionError preservato invariato
# =============================================================================


def test_err_msg_execution_error_branch_preserved():
    """Il ramo `isinstance(e, ExecutionError): err_msg = str(e)` deve restare
    invariato — ExecutionError e' domain exception costruita dal codice
    applicativo (message controllato), NON leak di internals."""
    src = _read_src()
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


# =============================================================================
# TEST 3 — _handle_failure failed branch ha token canonico schedule_execution_fatal
# =============================================================================


def test_handle_failure_failed_branch_has_canonical_error_token():
    """Il return dict del branch `failed` in `_handle_failure` deve contenere
    `"error": "schedule_execution_fatal"` (token machine-readable coerente
    con pattern 7.c). Il branch `retry` resta invariato (decisione ratificata:
    niente token retry in questo batch)."""
    src = _read_src()
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
    src = _read_src()
    assert "schedule_execution_retry" not in src, (
        "token schedule_execution_retry NON doveva essere aggiunto in 7.c-bis"
    )
