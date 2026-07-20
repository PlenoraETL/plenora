"""
Contratto B4 (roadmap accoppiamento): Column come contratto shared.

La sede canonica del modello Column e' ``app.shared.execution.canvas_column``;
``projects.canvas_models`` lo usa internamente senza riesportarlo.
``output_contract`` non dipende piu' dal dominio projects: il 2-ciclo
``projects <-> output_contract`` e' spezzato e non deve riformarsi.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_OUTPUT_CONTRACT_ROOT = _BACKEND_ROOT / "app" / "core" / "output_contract"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_canvas_models_does_not_reexport_column():
    """I consumer devono importare Column dalla sede shared canonica."""
    from app.core.projects import canvas_models

    assert not hasattr(canvas_models, "Column")


def test_shared_canvas_column_is_leaf_contract():
    """Il contratto shared non importa nulla da app.core (foundation)."""
    path = _BACKEND_ROOT / "app" / "shared" / "execution" / "canvas_column.py"
    offending = sorted(
        m for m in _imports(path) if m.startswith("app.core")
    )
    assert offending == [], f"canvas_column deve restare leaf: {offending}"


def test_output_contract_does_not_import_projects_domain():
    """B4: spezzato il 2-ciclo projects <-> output_contract — il dominio
    output_contract non deve riprendere dipendenze da projects."""
    violations: dict[str, list[str]] = {}
    for path in sorted(_OUTPUT_CONTRACT_ROOT.rglob("*.py")):
        offending = sorted(
            m
            for m in _imports(path)
            if m == "app.core.projects" or m.startswith("app.core.projects.")
        )
        if offending:
            violations[path.name] = offending

    assert violations == {}, (
        "app.core.output_contract non deve importare app.core.projects; "
        f"i contratti strutturali vivono in app.shared. Violazioni: {violations}"
    )
