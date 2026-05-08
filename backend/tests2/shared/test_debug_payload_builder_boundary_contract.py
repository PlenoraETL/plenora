from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.projects import debug_context
from app.utils.debug_payload_builder import DebugPayloadBuilder


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_debug_payload_builder_uses_project_debug_context_boundary() -> None:
    path = Path("backend/app/utils/debug_payload_builder.py")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    raw_project_db_imports: list[str] = []
    debug_context_imports: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "app.core.projects.db":
                raw_project_db_imports.append(
                    f"from app.core.projects.db import ... at line {node.lineno}"
                )
            if node.module == "app.core.projects":
                names = {alias.name for alias in node.names}
                if "debug_context" in names:
                    debug_context_imports.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(alias.name == "app.core.projects.db" for alias in node.names):
                raw_project_db_imports.append(
                    f"import app.core.projects.db at line {node.lineno}"
                )

    assert not raw_project_db_imports, (
        "debug_payload_builder must load project data through "
        "app.core.projects.debug_context, not app.core.projects.db directly: "
        f"{raw_project_db_imports}"
    )
    assert debug_context_imports, (
        "debug_payload_builder should import the project-owned debug context boundary"
    )


def test_project_debug_context_loads_typed_project_row(monkeypatch) -> None:
    row = SimpleNamespace(
        id="proj-1",
        name="Demo",
        project_type="arricchisci",
        canvas={"nodes": [], "edges": []},
    )
    monkeypatch.setattr(debug_context.project_db, "get_project_row", lambda pid: row)

    context = debug_context.load_project_debug_context("proj-1")

    assert context == debug_context.ProjectDebugContext(
        project_id="proj-1",
        project_name="Demo",
        project_type="arricchisci",
        triggered_by="manual",
        canvas={"nodes": [], "edges": []},
    )


def test_debug_payload_builder_preserves_project_context_and_node_config(
    monkeypatch,
) -> None:
    canvas = {
        "nodes": [
            {
                "id": "source",
                "type": "estrai",
                "data": {"label": "Source", "color": "blue", "path": "/tmp/in.csv"},
            },
            {
                "id": "load",
                "type": "carica",
                "data": {"label": "Load", "color": "green", "target": "out.xlsx"},
            },
        ],
        "edges": [{"source": "source", "target": "load"}],
    }
    monkeypatch.setattr(
        debug_context,
        "load_project_debug_context",
        lambda project_id: debug_context.ProjectDebugContext(
            project_id=project_id,
            project_name="Demo Project",
            project_type="pipeline",
            triggered_by="manual",
            canvas=canvas,
        ),
    )

    payload = DebugPayloadBuilder().build(
        run_id="run-1",
        project_id="proj-1",
        steps=[{"step": 1, "status": "success"}],
        failure={"node_id": "load"},
    )

    assert payload["pipeline_context"] == {
        "project_id": "proj-1",
        "project_name": "Demo Project",
        "module": "pipeline",
        "triggered_by": "manual",
        "canvas_summary": "Source \u2192 Load",
    }
    assert payload["failing_node_config"] == {"target": "out.xlsx"}


def test_debug_payload_builder_missing_project_matches_legacy_defaults(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        debug_context,
        "load_project_debug_context",
        lambda project_id: None,
    )

    payload = DebugPayloadBuilder().build(
        run_id="run-1",
        project_id="missing",
        steps=[],
        failure={"node_id": "missing-node"},
    )

    assert payload["pipeline_context"] == {
        "project_id": "missing",
        "project_name": "missing",
        "module": "",
        "triggered_by": "manual",
        "canvas_summary": "non disponibile",
    }
    assert payload["failing_node_config"] == {}
