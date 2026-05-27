"""
7.c — sanitize str(e) in Celery task result dict.

Audit finding:
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def _read(relpath: str) -> str:
    return (_BACKEND_ROOT / relpath).read_text(encoding="utf-8")


# SITO 1 — schedule_task.py (schedule_pipeline_task top-level)


def test_schedule_task_has_canonical_token_and_message():
    src = _read("workers/tasks/schedule_task.py")
    assert '"error": "schedule_task_fatal"' in src
    assert '"message": "Errore fatale del task schedule (consulta log)"' in src


def test_schedule_task_return_dict_no_longer_interpolates_str_e():
    """La vecchia stringa `f"Errore fatale: {str(e)}"` non deve piu' essere
    presente come value di `message` in alcun return dict."""
    src = _read("workers/tasks/schedule_task.py")
    # Pattern Previous behavior: `"message": f"Errore fatale: {str(e)}"`
    assert 'f"Errore fatale: {str(e)}"' not in src
    assert "f'Errore fatale: {str(e)}'" not in src


# SITO 2 — carica_task.py:229-273 (outer task CARICA)


def test_carica_task_outer_has_canonical_token():
    src = _read("workers/tasks/carica_task.py")
    assert 'public_error_code = "carica_task_fatal"' in src
    assert 'public_error_message = "Errore task CARICA (consulta log)"' in src
    assert '"error": public_error_code' in src
    assert '"message": public_error_message' in src
    assert '"error_message": public_error_message' in src


def test_carica_task_outer_preserves_error_details_traceback():
    """`error_details: error_trace` resta nel return dict come canale
    diagnostic separato (traceback strutturato, non mostrato user-facing
    direttamente)."""
    src = _read("workers/tasks/carica_task.py")
    assert '"error_details": error_trace' in src


def test_carica_task_outer_preserves_admin_internal_message():
    """error_msg_internal (con str(e)) continua a essere passato a close_run,
    _update_progress, log_service — canali admin diagnostics intenzionali."""
    src = _read("workers/tasks/carica_task.py")
    # error_msg_internal: nuova variabile sanitize che preserva str(e)
    # destinata solo a canali admin.
    assert "error_msg_internal" in src
    assert 'error_message=error_msg_internal' in src


# SITO 3 — carica_task.py:357-365 (DataSource write)


def test_carica_task_datasource_has_canonical_token():
    src = _read("workers/tasks/carica_task.py")
    assert "'error': 'carica_datasource_write_failed'" in src
    assert "'message': 'Errore scrittura DataSource (consulta log)'" in src
    assert "'error_message': 'Errore scrittura DataSource (consulta log)'" in src


def test_carica_task_datasource_no_str_e_in_return():
    """Il vecchio `'error_message': str(e)` non deve piu' esistere nel return
    dict del branch DataSource write."""
    src = _read("workers/tasks/carica_task.py")
    # Pattern Previous behavior: `'error_message': str(e),` — verificare assenza.
    # Il canale `DebugPayloadBuilder(failure={..., "error_message": str(e)})`
    # resta legittimo (keyword arg nested dict, non return dict).
    # Distinguiamo cercando specifico pattern "linea-intera" del return.
    pattern = re.compile(r"^\s*'error_message': str\(e\),\s*$", re.MULTILINE)
    assert not pattern.search(src), (
        "Trovata 'error_message': str(e) come linea top-level — Previous behavior pattern"
    )


# SITO 4 — carica_task.py:511-521 (ArcGIS write)


def test_carica_task_arcgis_has_canonical_token():
    src = _read("workers/tasks/carica_task.py")
    assert '"error": "carica_arcgis_write_failed"' in src
    assert '"message": "Errore scrittura ArcGIS (consulta log)"' in src
    assert '"error_message": "Errore scrittura ArcGIS (consulta log)"' in src


def test_carica_task_arcgis_old_interpolation_removed():
    """La vecchia stringa `f"Errore scrittura ArcGIS: {str(e)}"` non deve
    piu' comparire (era assegnata a error_msg usato in message/error_message)."""
    src = _read("workers/tasks/carica_task.py")
    assert 'f"Errore scrittura ArcGIS: {str(e)}"' not in src


# SITO 5 — imdf_publish_task.py:260-268


def test_imdf_publish_task_has_canonical_token():
    src = _read("workers/tasks/imdf_publish_task.py")
    assert '"error": "imdf_publish_failed"' in src
    assert '"message": "Errore pubblicazione IMDF (consulta log)"' in src


def test_imdf_publish_task_no_str_e_in_error_field():
    """Il vecchio `"error": str(e),` nel return dict non deve piu' esistere."""
    src = _read("workers/tasks/imdf_publish_task.py")
    pattern = re.compile(r'^\s*"error": str\(e\),\s*$', re.MULTILINE)
    assert not pattern.search(src)


# SITO 6 — execution_manager.py:883-885 (_execute_carica)


def test_execution_manager_execute_carica_has_canonical_token():
    src = _read("app/core/scheduler/execution_manager.py")
    assert '"error": "carica_scheduler_failed"' in src
    assert '"message": "Errore esecuzione CARICA scheduler (consulta log)"' in src


def test_execution_manager_execute_carica_no_bare_str_e_return():
    """Il vecchio `return {"success": False, "error": str(e)}` del branch
    broad `except Exception` (linee 883-885 Previous behavior) non deve piu' comparire.
    Il catch `except ArtifactResolutionError` (linea 802) resta fuori scope
    7.c come da audit §2.4 (boundary-classe-nota, candidato futuro)."""
    src = _read("app/core/scheduler/execution_manager.py")
    # Conta occorrenze: Current contract solo la 802 (ArtifactResolutionError branch)
    # deve restare, mai piu' di 1.
    count = src.count('return {"success": False, "error": str(e)}')
    assert count <= 1, (
        f"Trovate {count} occorrenze di `return {{\"success\": False, "
        f"\"error\": str(e)}}` — attesa al massimo 1 (branch "
        f"ArtifactResolutionError scope-fuori 7.c)."
    )


# ANTI-REGRESSIONE AGGREGATA — nessun "error": str(e) nei return dict top-level


def test_no_str_e_in_return_dict_error_field_across_all_sites():
    """Su tutti i 4 file coinvolti, nessun return dict in branch `except
    Exception` broad deve avere `"error": str(e)` o `'error': str(e)`.

    Whitelist scope-ratificato 7.c:
      - `execution_manager.py` catch specifico `ArtifactResolutionError`
        (boundary-classe-nota per audit §2.4, fuori dai 6 siti §3.1). Potra'
        essere ratificato in un batch successivo dedicato. Il numero di linea
        puo' slittare (vedi 7.b che ha aggiunto imports e retry wrapper);
        whitelistiamo per contenuto invece che lineno.
    """
    files = [
        "workers/tasks/schedule_task.py",
        "workers/tasks/carica_task.py",
        "workers/tasks/imdf_publish_task.py",
        "app/core/scheduler/execution_manager.py",
    ]
    # Whitelist per contenuto: la linea che contiene la stringa marker
    # (catch specifico ArtifactResolutionError) resta fuori scope 7.c.
    whitelist_marker = 'return {"success": False, "error": str(e)}'
    violations = []
    pattern = re.compile(r'''["']error["']:\s*str\(e\)''')
    for rel in files:
        src = _read(rel)
        for lineno, line in enumerate(src.splitlines(), start=1):
            if pattern.search(line):
                # Escludi linee dentro DebugPayloadBuilder failure={...}
                if "failure" in line or "error_type" in line:
                    continue
                # Whitelist per marker: catch ArtifactResolutionError
                # execution_manager → fuori scope 7.c (boundary classe-nota).
                if (
                    rel == "app/core/scheduler/execution_manager.py"
                    and whitelist_marker in line
                ):
                    continue
                violations.append(f"{rel}:{lineno}: {line.strip()}")
    assert not violations, (
        "Trovati pattern 'error': str(e) in return dict user-facing:\n"
        + "\n".join(violations)
    )


def test_all_6_canonical_tokens_present_across_files():
    """Sanity aggregata: tutti i 6 token canonici ratificati esistono almeno
    una volta ciascuno nel file atteso."""
    expectations = [
        ("workers/tasks/schedule_task.py", "schedule_task_fatal"),
        ("workers/tasks/carica_task.py", "carica_task_fatal"),
        ("workers/tasks/carica_task.py", "carica_datasource_write_failed"),
        ("workers/tasks/carica_task.py", "carica_arcgis_write_failed"),
        ("workers/tasks/imdf_publish_task.py", "imdf_publish_failed"),
        ("app/core/scheduler/execution_manager.py", "carica_scheduler_failed"),
    ]
    missing = []
    for rel, token in expectations:
        if token not in _read(rel):
            missing.append(f"{rel}: token '{token}' non trovato")
    assert not missing, "\n".join(missing)
