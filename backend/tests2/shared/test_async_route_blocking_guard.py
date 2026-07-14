"""Guard against synchronous I/O hidden inside async FastAPI routes."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def test_async_routes_have_real_await_points():
    core = Path(__file__).parents[2] / "app" / "core"
    offenders = []
    for path in sorted(core.rglob("*.py")):
        if path.name not in {"router.py", "routes.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(isinstance(child, ast.Await) for child in ast.walk(node)):
                offenders.append(f"{path.relative_to(core)}:{node.lineno}:{node.name}")

    assert offenders == [], (
        "Route async senza await bloccano l'event loop; dichiararle def per "
        f"usare il threadpool FastAPI: {offenders}"
    )
