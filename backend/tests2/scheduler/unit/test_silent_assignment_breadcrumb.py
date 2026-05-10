"""
7.f-bis — breadcrumb su silent-assignment fallback.

Residual sweep ε del punto 1 — 4 siti `except Exception: <assignment>`
(originariamente 5: il sito enrichment_task `_trigger_carica_if_present`
con context "group_id metadata lookup fallito" e' stato rimosso come
dead code in commit 4236403a "Clean Python hygiene residuals" — la
variabile `group_id` non era piu' usata dopo il cleanup).
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




def test_no_bare_silent_assignment_in_target_files():
    """Scansione: nessun `except Exception:\\n    <identifier> = <...>` con
    assegnamento puro senza breadcrumb nei 4 file target (5 siti 7.f-bis).

    Il pattern puro Previous behavior era:
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




_BREADCRUMB_CONTEXTS = [
    ("workers/tasks/pipeline_task.py",
     "project info lookup fallito"),
    ("workers/tasks/carica_task.py",
     "DebugPayloadBuilder fallito"),
    ("workers/tasks/enrichment_task.py",
     "DebugPayloadBuilder fallito (enrichment)"),
    ("app/core/scheduler/scheduler_execution_runtime.py",
     "DebugPayloadBuilder fallito (scheduler)"),
]


def test_silent_assignment_sites_have_debug_breadcrumb():
    """Per ciascuno dei 4 siti, verifica presenza di `logger.debug(f"<context>:
    {type(e).__name__}: ...")` col context unico attribuito in ε. Il
    breadcrumb puo' essere su una singola riga o split su piu' righe
    (concatenazione f-string implicita): cerchiamo entrambi i frammenti
    indipendentemente."""
    missing = []
    for rel, context in _BREADCRUMB_CONTEXTS:
        src = _read(rel)
        # Frammento 1: stringa di context dentro un f-string passato a
        # logger.debug. Tollera split su piu' righe del messaggio.
        ctx_fragment = f'f"{context}: '
        # Frammento 2: type(e).__name__ usato per categorizzare la
        # eccezione (formattatore canonico ε).
        type_fragment = "{type(e).__name__}"
        # Devono coabitare nello stesso file, idealmente vicini.
        if ctx_fragment not in src or type_fragment not in src:
            missing.append(
                f"{rel}: breadcrumb atteso per context '{context}' non trovato"
            )
    assert not missing, (
        "Breadcrumb ε mancanti:\n" + "\n".join(missing)
    )




def test_silent_assignment_fallback_values_preserved():
    """Il fix ε e' additivo: aggiunge `as e:` + `logger.debug(...)` ma non
    cambia il valore di fallback assegnato. Verifica che i 4 fallback
    canonici siano ancora presenti Current contract."""
    # (file, fallback statement pattern atteso)
    fallback_patterns = [
        ("workers/tasks/pipeline_task.py",
         "return {}"),
        ("workers/tasks/carica_task.py",
         "debug_payload = None"),
        ("workers/tasks/enrichment_task.py",
         "_debug_payload = None"),
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
