"""
7.f — breadcrumb coverage su cleanup except: pass.

Audit finding: pass` cleanup nei
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


_TARGET_FILES = [
    "workers/tasks/schedule_task.py",
    "workers/tasks/pipeline_task.py",
    "workers/tasks/carica_task.py",
    "workers/tasks/enrichment_task.py",
    "app/core/scheduler/execution_manager.py",
]


def _read(relpath: str) -> str:
    return (_BACKEND_ROOT / relpath).read_text(encoding="utf-8")




def test_no_bare_except_pass_in_target_files():
    """Scansione: per ciascuno dei 5 file, conta occorrenze di
    `except Exception:\\n    pass` (bare, senza as e / senza breadcrumb).
    Post-7.f il count atteso e' 0.
    """
    # Pattern multiline: `except Exception:` seguito da linea con solo `pass`
    # (eventualmente con whitespace). Il `re.MULTILINE` e' cruciale perche'
    # il match deve essere su due linee consecutive.
    pattern = re.compile(
        r"except\s+Exception\s*:\s*\n\s*pass(\s*#[^\n]*)?\s*\n",
        re.MULTILINE,
    )
    violations = []
    for rel in _TARGET_FILES:
        src = _read(rel)
        for m in pattern.finditer(src):
            # Calcola numero di linea per reporting
            lineno = src[:m.start()].count("\n") + 1
            violations.append(f"{rel}:{lineno}: {m.group(0).strip()}")

    assert not violations, (
        "Trovati bare 'except Exception: pass' nei file target 7.f:\n"
        + "\n".join(violations)
    )




# Mapping (file, stringa-contesto-breadcrumb) dei siti attesi.
# La stringa-contesto deve essere unica nel file Current contract per isolarla.
#
# Contract: rimossi 2 contesti che vivevano nel
# pattern `_async_write` thread daemon di enrichment_task.py (api_enrich
# snapshot + result snapshot). Quel pattern non esiste piu': il routing
# passa per `record_*_snapshot` sync via la policy. I 2 contesti rimossi
# erano:
#   - "async intermediate write fallito"  (in _async_write body)
#   - "cleanup async write fallito"       (in _async_write_result body)
# Restano i 13 siti che NON dipendevano dal pattern async daemon.
_BREADCRUMB_CONTEXTS = [
    ("workers/tasks/schedule_task.py",
     "log emergenza post-crash fallito"),
    ("workers/tasks/pipeline_task.py",
     "close_run intermediate fallito post-crash"),
    ("workers/tasks/pipeline_task.py",
     "close_connection pipeline fallito{suffix}"),
    ("workers/tasks/carica_task.py",
     "group_id lookup fallito"),
    ("workers/tasks/carica_task.py",
     "close_connection intermediate fallito{suffix}"),
    ("workers/tasks/carica_task.py",
     "close_connection config fallito{suffix}"),
    ("workers/tasks/enrichment_task.py",
     "cleanup temp dir fallito"),
    ("workers/tasks/enrichment_task.py",
     "close_connection enrichment fallito{suffix}"),
    ("workers/tasks/enrichment_task.py",
     "close_run intermediate fallito (early return)"),
    ("workers/tasks/enrichment_task.py",
     "close_connection intermediate fallito (outer except)"),
    ("workers/tasks/enrichment_task.py",
     "progress update fallito"),
    ("app/core/scheduler/execution_manager.py",
     "close_connection intermediate fallito"),
    ("app/core/scheduler/execution_manager.py",
     "close_connection config fallito"),
]


def test_each_cleanup_site_has_logger_debug_breadcrumb():
    """Per ciascuno dei 15 siti, verifica che esista una linea
    `logger.debug(f"<context>: {type(e).__name__}: {e}")` col contesto
    unico attribuito in checkpoint."""
    missing = []
    for rel, context in _BREADCRUMB_CONTEXTS:
        src = _read(rel)
        context_pos = src.find(context)
        logger_pos = src.rfind("logger.debug", 0, context_pos)
        breadcrumb_tail = src[context_pos:context_pos + 300]
        if (
            context_pos == -1
            or logger_pos == -1
            or context_pos - logger_pos > 120
            or "type(" not in breadcrumb_tail
        ):
            missing.append(
                f"{rel}: breadcrumb atteso per context '{context}' non trovato"
            )
    assert not missing, (
        "Breadcrumb 7.f mancanti:\n" + "\n".join(missing)
    )




def test_cleanup_sites_count_matches_scope():
    """Sanity aggregata: conta i contesti cleanup attesi nei 5 file.

    Contract: soglia abbassata da 15 a 13 dopo la rimozione del
    pattern `_async_write` thread daemon in enrichment_task.py (vedi
    `_BREADCRUMB_CONTEXTS` per il dettaglio). Restano i 13 siti
    cleanup non-async, piu' eventuali altri debug pre-esistenti dei
    current contract.c/7.b.
    """
    total_breadcrumbs = 0
    per_file: dict[str, int] = {}
    for rel in _TARGET_FILES:
        src = _read(rel)
        contexts = [context for path, context in _BREADCRUMB_CONTEXTS if path == rel]
        count = sum(src.count(context) for context in contexts)
        per_file[rel] = count
        total_breadcrumbs += count

    assert total_breadcrumbs >= len(_BREADCRUMB_CONTEXTS), (
        f"Atteso >={len(_BREADCRUMB_CONTEXTS)} breadcrumb cleanup, "
        f"ricevuto {total_breadcrumbs}.\n"
        f"Per-file: {per_file}"
    )
