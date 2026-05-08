from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EXECUTOR_PATH = BACKEND_ROOT / "app" / "core" / "manipola" / "executor.py"


def test_manipola_executor_does_not_import_projects_db_directly() -> None:
    """Project DB lookups for execution context stay behind projects boundary."""
    tree = ast.parse(EXECUTOR_PATH.read_text(encoding="utf-8"))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "app.core.projects.db":
            offenders.append(node.module)
        elif isinstance(node, ast.Import):
            offenders.extend(
                alias.name
                for alias in node.names
                if alias.name == "app.core.projects.db"
            )

    assert offenders == []
