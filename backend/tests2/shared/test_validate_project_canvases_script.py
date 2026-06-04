"""Contract tests for scripts/validate_project_canvases.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests2.shared._ast_identifiers import uses_attribute


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _BACKEND_ROOT / "scripts" / "validate_project_canvases.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "validate_project_canvases_script",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_script_reports_zero_when_all_canvases_are_valid(capsys) -> None:
    script = _load_script()
    rows = [
        {"id": "p-ok", "name": "Healthy", "canvas": '{"nodes": [], "edges": []}'},
        {"id": "p-empty", "name": "Empty", "canvas": None},
    ]

    exit_code = script.run(lambda: rows)

    captured = capsys.readouterr()
    assert exit_code == script.NO_INVALID_CANVASES
    assert "OK: scanned 2 project canvases; none invalid." in captured.out
    assert captured.err == ""


def test_script_reports_invalid_json_with_project_identity(capsys) -> None:
    script = _load_script()
    rows = [
        {"id": "p-bad", "name": "Broken Canvas", "canvas": '{"nodes": ['},
    ]

    exit_code = script.run(lambda: rows)

    captured = capsys.readouterr()
    assert exit_code == script.INVALID_CANVASES
    assert "project_id='p-bad'" in captured.err
    assert "name='Broken Canvas'" in captured.err
    assert "invalid JSON" in captured.err


def test_script_reports_structural_canvas_failure(capsys) -> None:
    script = _load_script()
    rows = [
        {
            "id": "p-struct",
            "name": "Bad Node",
            "canvas": {"nodes": [{"type": "result"}], "edges": []},
        },
    ]

    exit_code = script.run(lambda: rows)

    captured = capsys.readouterr()
    assert exit_code == script.INVALID_CANVASES
    assert "project_id='p-struct'" in captured.err
    assert "canvas.nodes contains an item" in captured.err


def test_script_reports_operational_failure(capsys) -> None:
    script = _load_script()

    def _raise_db_down():
        raise RuntimeError("db down")

    exit_code = script.run(_raise_db_down)

    captured = capsys.readouterr()
    assert exit_code == script.OPERATIONAL_FAILURE
    assert "ERROR: operational failure: RuntimeError: db down" in captured.err


def test_script_source_is_read_only_and_uses_canvas_graph() -> None:
    source = _SCRIPT_PATH.read_text(encoding="utf-8")

    assert uses_attribute(source, "CanvasGraph", "from_canvas")
    # RAW: SQL string literal content — not an identifier
    assert "SELECT id, name, canvas" in source
    banned_write_markers = (
        # RAW: SQL statement fragments — not identifiers
        "UPDATE projects",
        "DELETE FROM projects",
        "INSERT INTO projects",
        # RAW: call-pattern strings with trailing "(" — not pure identifiers
        "update_project(",
        "delete_project(",
        "create_project(",
        "execute(",
    )
    offenders = [marker for marker in banned_write_markers if marker in source]
    assert not offenders
