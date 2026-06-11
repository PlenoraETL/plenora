"""
Guardia "non peggiorare" sul grafo import cross-domain di app/core.

Roadmap accoppiamento, Epic A (dependency matrix + baseline SCC): il grafo
corrente viene ricalcolato con scripts/dependency_graph.py e confrontato con
la baseline versionata in tests2/shared/config/core_dependency_baseline.json.

La guardia fallisce SOLO su nuovo coupling (edge nuovi, nuovi domini dentro
un ciclo), mai sul debito storico gia' censito. Rimuovere un edge non rompe
il test: rigenerare la baseline nello stesso PR per consolidare il progresso:

    python scripts/dependency_graph.py --write-baseline \
        tests2/shared/config/core_dependency_baseline.json

Documento architetturale: docs/architecture/dependency-matrix.md.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_BASELINE_PATH = Path(__file__).parent / "config" / "core_dependency_baseline.json"
_REGEN_HINT = (
    "python scripts/dependency_graph.py --write-baseline "
    "tests2/shared/config/core_dependency_baseline.json"
)


def _load_graph_module():
    script = _BACKEND_ROOT / "scripts" / "dependency_graph.py"
    spec = importlib.util.spec_from_file_location("dependency_graph", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def current_report() -> dict:
    mod = _load_graph_module()
    return mod.build_report(_BACKEND_ROOT / "app" / "core")


@pytest.fixture(scope="module")
def baseline() -> dict:
    return json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))


def test_no_new_cross_domain_edges(current_report: dict, baseline: dict) -> None:
    """Ogni edge dominio->dominio nuovo rispetto alla baseline e' coupling
    nuovo: o si rimuove l'import, o lo si motiva nella dependency matrix e
    si rigenera la baseline nello stesso PR."""
    new_edges = sorted(set(current_report["edges"]) - set(baseline["edges"]))
    assert new_edges == [], (
        "Nuovi import cross-domain non censiti nella baseline: "
        f"{new_edges}. Se il coupling e' intenzionale, documentarlo in "
        f"docs/architecture/dependency-matrix.md e rigenerare: {_REGEN_HINT}"
    )


def test_no_new_domains_in_cycles(current_report: dict, baseline: dict) -> None:
    """Nessuna coppia di domini puo' diventare ciclica se non lo era gia':
    le SCC possono solo restringersi."""
    baseline_cyclic_pairs = {
        frozenset((a, b))
        for scc in baseline["sccs"]
        for a in scc
        for b in scc
        if a != b
    }
    violations = sorted(
        tuple(sorted(pair))
        for scc in current_report["sccs"]
        if len(scc) > 1
        for i, a in enumerate(scc)
        for b in scc[i + 1 :]
        if (pair := frozenset((a, b))) not in baseline_cyclic_pairs
    )
    assert violations == [], (
        "Nuove coppie di domini in ciclo rispetto alla baseline: "
        f"{violations}. Spezzare la dipendenza o, se davvero inevitabile, "
        f"motivarla e rigenerare: {_REGEN_HINT}"
    )


def test_baseline_not_stale_on_major_progress(current_report: dict, baseline: dict) -> None:
    """Igiene della baseline: se piu' di 10 edge censiti sono spariti dal
    codice, il debito reale e' molto sotto la baseline e va consolidata
    (rigenerarla evita che il margine venga riconsumato in silenzio)."""
    removed = sorted(set(baseline["edges"]) - set(current_report["edges"]))
    assert len(removed) <= 10, (
        f"{len(removed)} edge della baseline non esistono piu' nel codice "
        f"({removed[:5]}...). Consolidare il progresso: {_REGEN_HINT}"
    )
