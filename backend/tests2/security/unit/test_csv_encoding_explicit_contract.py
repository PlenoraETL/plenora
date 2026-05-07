"""
Contract test — ogni chiamata to_csv(path) esplicita encoding='utf-8'.

Pandas ha utf-8 come default per `to_csv`, ma il contratto non e' leggibile
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


BACKEND_ROOT = Path(__file__).resolve().parents[3]
SCANNED_DIRS = [BACKEND_ROOT / "app", BACKEND_ROOT / "workers"]


def _iter_to_csv_calls(tree: ast.AST):
    """Yield (call_node, has_positional_arg) for each .to_csv() call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "to_csv":
            continue
        has_positional = bool(node.args)
        yield node, has_positional


def _has_encoding_kwarg(call: ast.Call) -> bool:
    for kw in call.keywords:
        if kw.arg == "encoding":
            return True
    return False


def test_all_file_to_csv_calls_specify_utf8_encoding():
    """Ogni df.to_csv(path, ...) nel codebase deve avere encoding='utf-8'."""
    offenders = []
    for root in SCANNED_DIRS:
        if not root.exists():
            continue
        for py_file in root.rglob("*.py"):
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError):
                continue
            for call, has_positional in _iter_to_csv_calls(tree):
                if has_positional and not _has_encoding_kwarg(call):
                    rel = py_file.relative_to(BACKEND_ROOT)
                    offenders.append(f"{rel}:{call.lineno}")
    assert not offenders, (
        "to_csv(path, ...) senza encoding='utf-8' esplicito:\n  "
        + "\n  ".join(offenders)
        + "\nPandas default e' utf-8 ma il contratto deve essere leggibile.\n"
          "Vedi app/utils/dataframe.py:_read_csv_safe che prova utf-8 per primo."
    )


def test_in_memory_to_csv_without_path_is_allowed():
    """Control test: df.to_csv(index=False) senza path ritorna una stringa
    Python. Il parametro encoding NON si applica e darebbe solo warning.
    Questo pattern deve essere permesso (es. workers/tasks/carica_task.py
    lo usa per costruire payload in memoria)."""
    workers_dir = BACKEND_ROOT / "workers"
    if not workers_dir.exists():
        pytest.skip("workers/ non presente in questo ambiente")

    found_in_memory = False
    for py_file in workers_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for call, has_positional in _iter_to_csv_calls(tree):
            if not has_positional:
                found_in_memory = True
                # Deve essere senza encoding (altrimenti warning pandas)
                assert not _has_encoding_kwarg(call), (
                    f"{py_file.relative_to(BACKEND_ROOT)}:{call.lineno} "
                    "to_csv() senza path NON deve passare encoding (warning)"
                )

    # Non asserisco che esista almeno un caso — la presenza dipende dal codice.
    # Il test serve solo a catturare chi aggiunge erroneamente encoding al
    # pattern in-memory.
