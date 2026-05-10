"""
Source-bound tombstone for the removed POST /api/logs/clear endpoint.

Contract test P-CLR -- POST /api/logs/clear rimosso (anti-reintroduzione).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


_ROUTER_PATH = (
    Path(__file__).resolve().parents[3]
    / "app" / "core" / "logs" / "router.py"
)


def _route_paths(tree: ast.AST) -> list[str]:
    """Collect every literal path passed to `@router.<verb>(...)` in the
    given module AST."""
    paths: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match `router.<verb>(...)` decorator-style calls.
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "router"
        ):
            continue
        # First positional arg is the path string.
        if node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            paths.append(node.args[0].value)
    return paths


def test_logs_clear_route_not_re_registered():
    """`logs/router.py` must not declare any verb on `/clear`.

    The endpoint was deleted by P-CLR. Re-adding it under any HTTP verb
    is the canonical regression mode this test exists to block.
    """
    tree = ast.parse(_ROUTER_PATH.read_text(encoding="utf-8"))
    offending = [p for p in _route_paths(tree) if p == "/clear"]
    assert not offending, (
        f"REGRESSIONE P-CLR: `@router.<verb>(\"/clear\")` re-introduced "
        f"in {_ROUTER_PATH.name}. Found {len(offending)} occurrence(s). "
        "The /logs/clear endpoint is removed because the trigger "
        "plenora.block_log_modifications "
        "rejects every DELETE without the beat-worker bypass. Re-adding "
        "this endpoint -- even as a no-op or compat shim -- requires "
        "explicit team review and a paired O-ARC architecture decision. "
        "If the re-introduction is intentional, delete this test and "
        "document the new owner of plenora.logs DELETE in "
        "invariants.md sec. 1.4."
    )
