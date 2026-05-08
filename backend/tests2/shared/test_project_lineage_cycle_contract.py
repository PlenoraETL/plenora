"""
Funzione pura `detect_project_lineage_cycle` (C3 fix 5).

Rileva cicli nel DAG delle dipendenze cross-project (nodi
"""

from typing import Dict, List, Optional

import pytest

from app.core.projects.lineage import (
    detect_project_lineage_cycle,
    ProjectLineageCycleError,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def _sources_fn_from(graph: Dict[str, List[str]]):
    """Helper: costruisce get_sources_fn da un dict {project_id: [source_id, ...]}."""
    def fn(pid: str) -> Optional[List[str]]:
        return graph.get(pid, [])
    return fn


# No cycle


def test_linear_chain_has_no_cycle():
    """A depends on B depends on C — no cycle."""
    graph = {"A": ["B"], "B": ["C"], "C": []}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is None


def test_project_with_no_sources_has_no_cycle():
    """Progetto root senza project_source: no ciclo."""
    graph = {"A": []}
    assert detect_project_lineage_cycle("A", _sources_fn_from(graph)) is None


def test_diamond_pattern_is_not_a_cycle():
    """A → B → D, A → C → D. D appare in due path ma NON torna su A.
    Questo NON e' un ciclo — un DAG puo' riusare nodi downstream."""
    graph = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": []}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is None


def test_unrelated_subtree_is_ignored():
    """Solo le dipendenze raggiungibili da root sono considerate."""
    graph = {
        "A": ["B"], "B": [],
        # X → Y → X sono un ciclo separato ma irraggiungibile da A
        "X": ["Y"], "Y": ["X"],
    }
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is None


# Cycle detection


def test_detects_self_reference():
    """A → A: ciclo diretto."""
    graph = {"A": ["A"]}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is not None
    assert result[0] == "A"
    assert result[-1] == "A"


def test_detects_two_node_cycle():
    """A → B → A."""
    graph = {"A": ["B"], "B": ["A"]}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is not None
    # La catena deve contenere A e B e tornare su A
    assert "A" in result
    assert "B" in result
    assert result[-1] == "A"


def test_detects_three_node_cycle():
    """A → B → C → A."""
    graph = {"A": ["B"], "B": ["C"], "C": ["A"]}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is not None
    assert "A" in result
    assert "B" in result
    assert "C" in result
    assert result[-1] == "A"


def test_cycle_reached_through_intermediate_branch():
    """A → B, B → C, C → B (cycle B → C → B, raggiungibile da A)."""
    graph = {"A": ["B"], "B": ["C"], "C": ["B"]}
    result = detect_project_lineage_cycle("A", _sources_fn_from(graph))
    assert result is not None
    # Deve contenere B e C (non necessariamente A — il cycle e' interno)
    assert "B" in result
    assert "C" in result


# Error class


def test_cycle_error_has_readable_chain_attribute():
    """ProjectLineageCycleError esposto con `chain` leggibile."""
    err = ProjectLineageCycleError(["A", "B", "A"])
    assert err.chain == ["A", "B", "A"]
    # Il str() dell'errore deve mostrare la catena in forma leggibile
    msg = str(err)
    assert "A" in msg and "B" in msg


# Robustness


def test_get_sources_fn_returning_none_treated_as_no_sources():
    """Se get_sources_fn ritorna None (progetto non trovato), equivale a []."""
    def fn(pid):
        return None if pid == "missing" else []
    result = detect_project_lineage_cycle("missing", fn)
    assert result is None


def test_handles_very_deep_chain_without_stack_overflow():
    """Una catena molto lunga non deve causare RecursionError.
    L'implementazione deve essere iterativa (o avere sys.setrecursionlimit
    adeguato)."""
    depth = 200
    graph = {}
    for i in range(depth):
        graph[f"n{i}"] = [f"n{i+1}"]
    graph[f"n{depth}"] = []

    result = detect_project_lineage_cycle("n0", _sources_fn_from(graph))
    assert result is None
