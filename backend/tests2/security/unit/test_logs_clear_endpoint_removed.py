"""Source-bound tombstone for the removed POST /api/logs/clear endpoint.

Contract test P-CLR -- POST /api/logs/clear rimosso (anti-reintroduzione).

Background: P-CLR removed `POST /logs/clear` (commit 698ff9d, 2026-04-22).
Log retention is now driven by the beat worker (O-ARC pipeline
archive-before-purge) as the only authorised consumer of DELETE on
`plenora.logs`. References:
  - `orchestrate/phase_2_hardening/p_clr_finalization.md` sec. 5.4;
  - `backend/docs/architecture/invariants.md` sec. 1.4.

This test blocks re-introduction: if anyone re-adds `@router.post("/clear")`
in `backend/app/core/logs/router.py` (even as a no-op or compat shim),
this source-bound check fails and forces explicit team review.

TEST-ENGINE-003 shrink: replaced the prior 100-line TestClient-based
form (3 HTTP round-trips: POST, GET, OpenAPI schema absence) with a
single AST scan of the router source. Equivalent regression coverage:
the only realistic path that would re-expose `/clear` is a future PR
that adds `@router.post("/clear")` (or any verb) back to
`logs/router.py`. The HTTP-side tests would have caught the same
re-introduction one boot earlier; an AST scan catches it at PR time.
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
        "The /logs/clear endpoint was removed (commit 698ff9d, "
        "2026-04-22) because the trigger plenora.block_log_modifications "
        "rejects every DELETE without the beat-worker bypass. Re-adding "
        "this endpoint -- even as a no-op or compat shim -- requires "
        "explicit team review and a paired O-ARC architecture decision. "
        "If the re-introduction is intentional, delete this test and "
        "document the new owner of plenora.logs DELETE in "
        "invariants.md sec. 1.4."
    )
