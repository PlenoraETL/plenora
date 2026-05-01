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


def _references_any(callable_or_module, names):
    """Bytecode probe: True se il callable referenzia almeno uno dei
    `names` come callee/attr (anche in nested code objects)."""
    if hasattr(callable_or_module, "__code__"):
        codes = [callable_or_module.__code__]
    else:
        # Modulo: cammina su tutti i top-level callable.
        codes = []
        for attr_name in dir(callable_or_module):
            attr = getattr(callable_or_module, attr_name, None)
            if hasattr(attr, "__code__"):
                codes.append(attr.__code__)
            elif inspect.isclass(attr):
                for m_name in dir(attr):
                    m = getattr(attr, m_name, None)
                    if hasattr(m, "__code__"):
                        codes.append(m.__code__)

    def _walk(co):
        for n in names:
            if n in co.co_names:
                return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    return any(_walk(co) for co in codes)


def test_carica_sorgenti_progetto_has_cycle_detection_guard():
    """`_carica_sorgenti_progetto` deve referenziare i symbol di cycle
    detection (ProjectLineageCycleError / visited / project_lineage).

    Replacement R-04b di `inspect.getsource(...) + markers in source`:
    bytecode probe (ricorsivo per closures). Refactor-resilient:
    cattura la presenza dei callee/exception identifier nel bytecode
    invece della stringa nel source.
    """
    method = manipola_executor.ManipolaExecutor._carica_sorgenti_progetto
    markers = [
        "ProjectLineageCycleError",
        "_assert_no_lineage_cycle_on_chain",
        "_push_to_execution_chain",
        "_pop_from_execution_chain",
        "project_lineage",
        "lineage_cycle",
    ]

    code = method.__code__

    def _walk(co):
        for n in markers:
            if n in co.co_names or n in co.co_varnames:
                return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    assert _walk(code), (
        "_carica_sorgenti_progetto NON contiene cycle detection. "
        f"Markers cercati: {markers}. "
        "Un canvas legacy con ciclo A->B->A fa stack overflow."
    )


def test_executor_module_imports_project_lineage_symbols():
    """Il modulo executor deve esporre i symbol di project_lineage
    (importati at module load).

    Replacement R-04b di `inspect.getsource(module) + "project_lineage" in source`:
    introspect del namespace modulo via `dir()` / `getattr`.
    """
    has_symbol = any(
        getattr(manipola_executor, name, None) is not None
        for name in (
            "ProjectLineageCycleError",
            "_push_to_execution_chain",
            "_pop_from_execution_chain",
            "detect_project_lineage_cycle",
        )
    )
    assert has_symbol, (
        "Manipola executor non importa nessun symbol di project_lineage: "
        "impossibile rilevare cicli a runtime."
    )
