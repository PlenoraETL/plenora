"""Arricchisci density audit guards."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from app.core.arricchisci.execution_context import ArricchisciExecutionContext


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ARRICCHISCI_ROOT = _BACKEND_ROOT / "app" / "core" / "arricchisci"


def _imports_for(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def test_rest_file_uses_files_boundary_for_file_source_path_materialization() -> None:
    """The reusable file-source cache resolver belongs to core.files."""
    imports = _imports_for(_ARRICCHISCI_ROOT / "api_connection_rest_file.py")

    assert "app.core.files.source_loading" in imports
    assert "app.core.files.db" not in imports


def test_rest_file_has_no_plain_keep_compat_marker() -> None:
    source = (_ARRICCHISCI_ROOT / "api_connection_rest_file.py").read_text(
        encoding="utf-8-sig",
    )

    marker = "KEEP" + "_COMPAT"
    assert marker not in source


def test_rest_file_path_resolver_remains_ctx_adapter_only() -> None:
    from app.core.arricchisci.api_connection_rest_file import (
        ApiConnectionRestFileMixin,
    )

    svc = ApiConnectionRestFileMixin()
    svc._current_group_id = "stale-grp-should-not-leak"
    ctx = ArricchisciExecutionContext(
        project_id="p-1",
        group_id="winner-grp",
        preview_limit=None,
    )

    with patch(
        "app.core.arricchisci.api_connection_rest_file.resolve_file_source_id_bytes",
        return_value=(b"csv", "input.csv"),
    ) as delegated:
        result = svc._resolve_file_source_id_path("fs-1", ctx=ctx)

    assert result == (b"csv", "input.csv")
    delegated.assert_called_once_with(
        "fs-1",
        expected_group_id="winner-grp",
    )
