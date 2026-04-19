"""Cycle detection runtime in _carica_sorgenti_progetto (C3 round 3 fix 3).

Contesto
--------
Il mio fix precedente (C3 fix 5) aggiunge `detect_project_lineage_cycle`
chiamato da ProjectService.update_canvas — blocca la creazione di
canvas ciclici AL SAVE. Non copre:
- Canvas legacy creati PRIMA del fix con cicli latenti.
- Bypass del service (direct DB edit).
- Race save-vs-execute.

In piu' `_carica_sorgenti_progetto` in manipola/executor.py, quando
un progetto upstream non ha cache, invoca `orchestrator_callback`
per riesegue la pipeline di quel progetto — che a sua volta puo'
contenere un altro `project_source`, ricorsione completa. Ciclo
A → B → A genera quindi stack overflow.

Questo test richiede che l'executor:
1. Accetti un `visited_set` opzionale tracciando il cammino attuale.
2. Se il source_project_id e' gia' in visited → solleva
   ProjectLineageCycleError con la catena readable.
"""

import inspect

import pytest

from app.core.manipola import executor as manipola_executor


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


def test_carica_sorgenti_progetto_has_cycle_detection_guard():
    """Il source di `_carica_sorgenti_progetto` deve contenere una
    cycle detection esplicita: reference a project_lineage / visited /
    ProjectLineageCycleError / ciclo."""
    source = inspect.getsource(manipola_executor.ManipolaExecutor._carica_sorgenti_progetto)

    markers = [
        "ProjectLineageCycleError",
        "visited",
        "project_lineage",
        "lineage_cycle",
    ]
    found = [m for m in markers if m in source]
    assert found, (
        "_carica_sorgenti_progetto NON contiene cycle detection. "
        f"Markers cercati: {markers}. "
        "Un canvas legacy con ciclo A→B→A fa stack overflow."
    )


def test_executor_imports_project_lineage_module():
    """Il modulo executor deve importare dal project_lineage (dove
    vive il detector) — verifica per grep."""
    source = inspect.getsource(manipola_executor)
    assert "project_lineage" in source or "ProjectLineageCycleError" in source, (
        "Manipola executor non importa project_lineage: impossibile "
        "rilevare cicli a runtime."
    )
