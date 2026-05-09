"""
[phase-2-err][residual ε] 7.f-bis — breadcrumb su silent-assignment fallback.

Residual sweep ε del punto 1 — 5 siti `except Exception: <assignment>`
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


_TARGET_FILES = [
    "workers/tasks/pipeline_task.py",
    "workers/tasks/carica_task.py",
    "workers/tasks/enrichment_task.py",
    "app/core/scheduler/execution_manager.py",
]


def _read(relpath: str) -> str:
    return (_BACKEND_ROOT / relpath).read_text(encoding="utf-8")


# TEST 1 — Zero bare silent-assignment nei 4 file target


def test_no_bare_silent_assignment_in_target_files():
    """Scansione: nessun `except Exception:\\n    <identifier> = <...>` con
    assegnamento puro senza breadcrumb nei 4 file target (5 siti 7.f-bis).

    Il pattern puro pre-fix era:
        except Exception:
            group_id = None
        (o return {}, = None, etc.)

    Post-ε il catch deve avere `as e:` + riga logger.debug precedente
    l'assignment. Il test conta bare silent-assignment senza `as e`.
    """
    # Pattern: `except Exception:` (NO `as e`) seguito da una linea con
    # un'assegnazione `<identifier> = ...` o `return <something>`.
    # Escludo righe con `pass` (quello e' 7.f gia' chiuso).
    pattern = re.compile(
        r"except\s+Exception\s*:\s*\n\s*([A-Za-z_][A-Za-z0-9_]*\s*=|return\s)",
        re.MULTILINE,
    )

    violations = []
    for rel in _TARGET_FILES:
        src = _read(rel)
        for m in pattern.finditer(src):
            lineno = src[:m.start()].count("\n") + 1
            violations.append(f"{rel}:{lineno}")

    assert not violations, (
        "Trovati bare silent-assignment (except Exception: <assign>) nei "
        "file target ε:\n" + "\n".join(violations)
    )


# TEST 2 — Ogni sito ha logger.debug con type(e).__name__


_BREADCRUMB_CONTEXTS = [
    ("workers/tasks/pipeline_task.py",
     "project info lookup fallito"),
    ("workers/tasks/carica_task.py",
     "DebugPayloadBuilder fallito"),
    ("workers/tasks/enrichment_task.py",
     "DebugPayloadBuilder fallito (enrichment)"),
    ("workers/tasks/enrichment_task.py",
     "group_id metadata lookup fallito"),
    ("app/core/scheduler/scheduler_execution_runtime.py",
     "DebugPayloadBuilder fallito (scheduler)"),
]


def test_silent_assignment_sites_have_debug_breadcrumb():
    """Per ciascuno dei 5 siti, verifica presenza di `logger.debug(f"<context>:
    {type(e).__name__}: ...")` col context unico attribuito in ε."""
    missing = []
    for rel, context in _BREADCRUMB_CONTEXTS:
        src = _read(rel)
        expected_fragment = (
            f'logger.debug(f"{context}: {{type('
        )
        if expected_fragment not in src:
            missing.append(
                f"{rel}: breadcrumb atteso per context '{context}' non trovato"
            )
    assert not missing, (
        "Breadcrumb ε mancanti:\n" + "\n".join(missing)
    )


# TEST 3 — Fallback values preservati (breadcrumb additivo, non cambia fallback)


def test_silent_assignment_fallback_values_preserved():
    """Il fix ε e' additivo: aggiunge `as e:` + `logger.debug(...)` ma non
    cambia il valore di fallback assegnato. Verifica che i 5 fallback
    canonici siano ancora presenti post-fix."""
    # (file, fallback statement pattern atteso)
    fallback_patterns = [
        ("workers/tasks/pipeline_task.py",
         "return {}"),
        ("workers/tasks/carica_task.py",
         "debug_payload = None"),
        ("workers/tasks/enrichment_task.py",
         "_debug_payload = None"),
        ("workers/tasks/enrichment_task.py",
         "group_id = None"),
        ("app/core/scheduler/scheduler_execution_runtime.py",
         "debug_payload = None"),
    ]

    missing = []
    for rel, fallback in fallback_patterns:
        src = _read(rel)
        if fallback not in src:
            missing.append(
                f"{rel}: fallback '{fallback}' non trovato post-ε (deve essere additivo)"
            )
    assert not missing, (
        "Fallback values rotti da ε:\n" + "\n".join(missing)
    )
