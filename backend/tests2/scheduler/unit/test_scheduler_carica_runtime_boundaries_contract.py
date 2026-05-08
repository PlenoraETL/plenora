"""Contract guards for scheduler/CARICA worker lifecycle boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.scheduler, pytest.mark.carica, pytest.mark.contract]


_RUNTIME_FILES = {
    "execution_manager": Path("backend/app/core/scheduler/execution_manager.py"),
    "carica_task": Path("backend/workers/tasks/carica_task.py"),
}


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("name", "path"), _RUNTIME_FILES.items())
def test_scheduler_carica_runtime_closes_intermediate_via_cleanup_boundary(name, path):
    tree = _tree(path)

    direct_close_imports = []
    cleanup_close_imports = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue

        imported_names = {alias.name for alias in node.names}
        if node.module == "app.core.intermediate.db" and "close_connection" in imported_names:
            direct_close_imports.append(node.lineno)
        if (
            node.module == "app.core.intermediate.cleanup"
            and "close_worker_connection" in imported_names
        ):
            cleanup_close_imports.append(node.lineno)

    assert not direct_close_imports, (
        f"{name} must close worker intermediate connections through "
        "app.core.intermediate.cleanup; direct db.close_connection import "
        f"found at lines {direct_close_imports}"
    )
    assert cleanup_close_imports, (
        f"{name} should import close_worker_connection from "
        "app.core.intermediate.cleanup"
    )
