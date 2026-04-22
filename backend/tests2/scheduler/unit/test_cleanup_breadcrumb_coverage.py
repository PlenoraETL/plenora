"""[phase-2-err][batch7] 7.f — breadcrumb coverage su cleanup except: pass.

Audit Batch 7 §3.4 [MEDIA] — 15 siti `except Exception: pass` cleanup nei
worker Celery e execution_manager violavano §5.1.4 Batch 1 kickoff: fail-open
senza breadcrumb, nessuna visibilita' in caso di fallimento ripetuto del
cleanup (close_connection, close_run, progress update, shutil.rmtree, ecc.).

Post-7.f:
  - Ogni catch puro ha ora `logger.debug(f"<context>: {type(e).__name__}: {e}")`.
  - Level `debug` (non warning) coerente con volumi alti e pattern already
    adottato in Batch 8 C2 (storage/provider breadcrumb).
  - Nessun cambio comportamento: cleanup resta best-effort, nessuna
    propagazione, nessun re-raise.

Approccio test source-based (come 7.c): i Celery tasks con decorator +
bind=True + thread-local DB fanno mock runtime fragili. Scansioniamo il
codice sorgente e blindiamo:
  1. Zero bare `except Exception:\n    pass` nei 5 file target.
  2. Per ciascuno dei 15 siti noti, presenza di `logger.debug` con
     `type(e).__name__` in prossimita'.
  3. Contatore siti coperti == 15 (sanity aggregata).

I 5 silent-assignment (pipeline_task:60, carica_task:256, enrichment_task:
1140, enrichment_task:1300, execution_manager:1026) restano fuori scope
come ratificato (7.f-bis debito).
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


# =============================================================================
# TEST 1 — Zero bare `except Exception: pass` nei 5 file target
# =============================================================================


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


# =============================================================================
# TEST 2 — Ogni sito ha logger.debug con type(e).__name__
# =============================================================================


# Mapping (file, stringa-contesto-breadcrumb) dei 15 siti attesi.
# La stringa-contesto deve essere unica nel file post-fix per isolarla.
_BREADCRUMB_CONTEXTS = [
    ("workers/tasks/schedule_task.py",
     "log emergenza post-crash fallito"),
    ("workers/tasks/pipeline_task.py",
     "close_run intermediate fallito post-crash"),
    ("workers/tasks/pipeline_task.py",
     "close_connection intermediate fallito post-crash"),
    ("workers/tasks/carica_task.py",
     "group_id lookup fallito"),
    ("workers/tasks/carica_task.py",
     "close_connection intermediate fallito"),  # anche presente in em
    ("workers/tasks/carica_task.py",
     "close_connection config fallito"),
    ("workers/tasks/enrichment_task.py",
     "async intermediate write fallito"),
    ("workers/tasks/enrichment_task.py",
     "cleanup temp dir fallito"),
    ("workers/tasks/enrichment_task.py",
     "cleanup async write fallito"),
    ("workers/tasks/enrichment_task.py",
     "cleanup async close_run fallito"),
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
        # Match linee `logger.debug(f"<context>: {type(e).__name__}: ...")`
        # con il context atteso.
        expected_fragment = (
            f'logger.debug(f"{context}: {{type('
        )
        if expected_fragment not in src:
            missing.append(
                f"{rel}: breadcrumb atteso per context '{context}' non trovato"
            )
    assert not missing, (
        "Breadcrumb 7.f mancanti:\n" + "\n".join(missing)
    )


# =============================================================================
# TEST 3 — Count aggregato: 15 siti trattati
# =============================================================================


def test_cleanup_sites_count_matches_scope():
    """Sanity aggregata: conta quante occorrenze di `logger.debug(f"`
    con `type(e).__name__` esistono nei 5 file. Atteso >= 15 (i 15 siti
    7.f + eventuali altri debug pre-esistenti del batch 7.c/7.b).
    """
    # Pattern flessibile: accetta `type(e)`, `type(e_inner)`, `type(e_prog)`,
    # ecc. (alcuni siti nested usano variable name diverso per evitare shadow).
    pattern = re.compile(
        r"""logger\.debug\(f"[^"]+:\s*\{type\([A-Za-z_][A-Za-z0-9_]*\)\.__name__\}""",
    )
    total_breadcrumbs = 0
    per_file: dict[str, int] = {}
    for rel in _TARGET_FILES:
        src = _read(rel)
        count = len(pattern.findall(src))
        per_file[rel] = count
        total_breadcrumbs += count

    # Minimo atteso = 15 siti 7.f. Altri batch potrebbero averne aggiunti.
    assert total_breadcrumbs >= 15, (
        f"Atteso >=15 breadcrumb cleanup, ricevuto {total_breadcrumbs}.\n"
        f"Per-file: {per_file}"
    )
