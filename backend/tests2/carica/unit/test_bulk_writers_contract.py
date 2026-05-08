"""Tombstone guard for the removed fake Carica bulk dispatcher."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]

ROOT = Path(__file__).resolve().parents[3]
CARICA_ROOT = ROOT / "app" / "core" / "carica"


def test_fake_bulk_dispatcher_module_is_removed():
    assert not (CARICA_ROOT / "bulk_writers.py").exists()


def test_writer_transaction_exec_has_no_bulk_dispatcher_imports():
    source = (CARICA_ROOT / "writer_transaction_exec.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    forbidden = {
        "app.core.carica.bulk_writers",
        "bulk_insert",
        "BulkInsertNotSupportedError",
        "is_bulk_enabled",
        "CARICA_BULK_INSERT",
    }

    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in forbidden:
                hits.append(module)
            hits.extend(alias.name for alias in node.names if alias.name in forbidden)
        elif isinstance(node, ast.Import):
            hits.extend(alias.name for alias in node.names if alias.name in forbidden)
        elif isinstance(node, ast.Name) and node.id in forbidden:
            hits.append(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in forbidden:
                hits.append(node.value)

    assert hits == []
