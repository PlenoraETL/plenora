"""
Contract test N8 — apply_runs_retention invocato dopo close_run.

Pre-fix: `apply_runs_retention()` in `intermediate/db.py:330` era definita
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.security, pytest.mark.contract]


def _references_name(callable_or_task, name: str) -> bool:
    """Bytecode probe: True se il callable referenzia `name` come
    callee/attr (anche in nested code objects). Refactor-resilient."""
    code = getattr(callable_or_task, "__wrapped__", callable_or_task).__code__

    def _walk(co) -> bool:
        if name in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const):
                return True
        return False

    return _walk(code)


def test_close_run_references_apply_runs_retention():
    """writer.close_run deve referenziare apply_runs_retention come callee.

    Replacement R-04b di `inspect.getsource(close_run) + "apply_runs_retention" in src`:
    bytecode probe sul callee identifier (refactor-resilient ai rinames
    locali). L'invariant ordering "DOPO update_run" e' coperto dal
    test funzionale spy `test_close_run_invokes_retention_with_correct_args`
    sotto, che asserisce direttamente l'ordine osservabile delle call.
    """
    from app.core.intermediate.writer import close_run

    assert _references_name(close_run, "apply_runs_retention"), (
        "writer.close_run non riferisce apply_runs_retention. La funzione "
        "esiste in intermediate/db.py ma nessuno la invoca -> disk fill "
        "nel tempo su intermediate.runs + node_meta + node_rows."
    )


# Test droppato (R-04b, 2026-05-01):
#   `test_retention_called_after_update_run_not_before` (pre-PR)
#   verificava `idx_retention > idx_update` cercando le stringhe nel
#   source. Ridondante con `test_close_run_invokes_retention_with_correct_args`
#   sotto che asserisce direttamente
#   `ops.index("apply_runs_retention") > ops.index("update_run")`
#   come ordine osservabile delle call (behavior-bound).


def test_close_run_invokes_retention_with_correct_args(monkeypatch):
    """Test funzionale: close_run fa list_node_meta + update_run + get_run +
    apply_runs_retention(canvas_id, group_id, retention_n) nell'ordine giusto."""
    from app.core.intermediate import writer as wr

    calls = []

    def spy_list_node_meta(run_id):
        calls.append(("list_node_meta", run_id))
        return [{"status": "ok", "row_count": 10}]

    def spy_update_run(run_id, **kwargs):
        calls.append(("update_run", run_id, kwargs.get("status")))

    def spy_get_run(run_id):
        calls.append(("get_run", run_id))
        return {"canvas_id": "proj_1", "group_id": "group_A"}

    def spy_retention(canvas_id, group_id, *, retention_n=None):
        calls.append(("apply_runs_retention", canvas_id, group_id, retention_n))

    monkeypatch.setattr(wr.db, "list_node_meta", spy_list_node_meta)
    monkeypatch.setattr(wr.db, "update_run", spy_update_run)
    monkeypatch.setattr(wr.db, "get_run", spy_get_run)
    monkeypatch.setattr(wr.db, "apply_runs_retention", spy_retention)
    monkeypatch.setattr(wr, "_resolve_runs_retention_n", lambda retention_n=None: 7)

    wr.close_run(run_id="r1", input_hash="a", output_hash="b",
                 total_exec_time=1.23, success=True, retention_n=7)

    # Sequenza attesa: list_node_meta -> update_run -> get_run -> apply_runs_retention
    ops = [c[0] for c in calls]
    assert "update_run" in ops
    assert "apply_runs_retention" in ops
    # retention DOPO update_run
    assert ops.index("apply_runs_retention") > ops.index("update_run")
    # retention con i canvas_id/group_id corretti dal get_run
    retention_call = next(c for c in calls if c[0] == "apply_runs_retention")
    assert retention_call[1:] == ("proj_1", "group_A", 7)


def test_retention_failure_does_not_propagate(monkeypatch):
    """Se apply_runs_retention esplode (DB lock, constraint, ecc.), la
    chiusura della run DEVE comunque ritornare senza propagare: un
    cleanup fallito non deve rompere il task."""
    from app.core.intermediate import writer as wr

    monkeypatch.setattr(wr.db, "list_node_meta", lambda run_id: [])
    monkeypatch.setattr(wr.db, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(wr.db, "get_run", lambda rid: {"canvas_id": "p", "group_id": "g"})

    def boom(*a, **k):
        raise RuntimeError("DB lock during retention")
    monkeypatch.setattr(wr.db, "apply_runs_retention", boom)

    # Non deve sollevare — il fallimento e' loggato come warning
    wr.close_run(run_id="r1", input_hash="a", output_hash="b",
                 total_exec_time=1.0, success=True)


def test_apply_runs_retention_signature_stable():
    """Signature stabile di apply_runs_retention: (canvas_id, group_id, retention_n)."""
    from app.core.intermediate.db import apply_runs_retention

    sig = inspect.signature(apply_runs_retention)
    params = list(sig.parameters.keys())
    assert params[:3] == ["canvas_id", "group_id", "retention_n"], (
        f"apply_runs_retention signature cambiata: {params}. "
        "Breaking per writer.close_run che passa la policy di retention."
    )


_BACKEND_ROOT = Path(__file__).resolve().parents[3]


def test_intermediate_db_does_not_read_project_retention_policy():
    """La policy projects.retention_n non deve vivere dentro intermediate.db."""
    import ast

    src_path = _BACKEND_ROOT / "app" / "core" / "intermediate" / "db.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "app.core.projects.db"
        elif isinstance(node, ast.Import):
            assert all(alias.name != "app.core.projects.db" for alias in node.names)


def test_intermediate_writer_does_not_read_project_retention_policy():
    """La policy projects.retention_n viene passata al writer dagli orchestration edge."""
    import ast

    src_path = _BACKEND_ROOT / "app" / "core" / "intermediate" / "writer.py"
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert node.module != "app.core.projects.db"
        elif isinstance(node, ast.Import):
            assert all(alias.name != "app.core.projects.db" for alias in node.names)
