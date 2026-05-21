"""
Contract test per il fix TOCTOU del trigger backup manuale.

Bug coperto (plenora-bug-admin-api P0):
  `trigger_manual_backup` aveva un branch di retry separato che
  chiamava `is_manual_backup_running()` + secondo
  `try_acquire_manual_backup_lock` come due query non serializzate.
  Tra le due c'era una finestra di race: due admin che cliccavano
  "Backup ora" nello stesso istante potevano entrambi superare il gate.

Post-fix: una sola chiamata atomica `try_acquire_manual_backup_lock`.
Se ritorna None, si risponde direttamente "gia' in corso" senza ulteriori
query (`INSERT ... ON CONFLICT DO UPDATE WHERE expires_at <= now()` e'
gia' atomica per definizione).

Test AST-based: verifica che `is_manual_backup_running` non sia piu'
referenziata in `trigger_manual_backup` (la presenza implica retro-
regressione del fix).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.admin_backups import service as backups_service


pytestmark = [
    pytest.mark.unit,
    pytest.mark.security,
    pytest.mark.contract,
    pytest.mark.regression,
]


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


class TestTriggerManualBackupNoToctou:
    """Garantisce che `trigger_manual_backup` non chiami piu'
    `is_manual_backup_running` ne' faccia un secondo `try_acquire_...`
    nel branch di "lock gia' detenuto".
    """

    def test_function_does_not_reference_is_manual_backup_running(self):
        src_path = Path(backups_service.__file__).resolve()
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        fn = _find_function(tree, "trigger_manual_backup")
        assert fn is not None

        called_names = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    getattr(func, "id", None)
                    or getattr(func, "attr", None)
                )
                if name:
                    called_names.append(name)

        assert "is_manual_backup_running" not in called_names, (
            "Pre-fix `trigger_manual_backup` chiamava "
            "`is_manual_backup_running` come secondo step non atomico "
            "rispetto a try_acquire — race TOCTOU. Il fix richiede una "
            "sola chiamata atomica al lock."
        )

    def test_try_acquire_called_exactly_once(self):
        """Il fix richiede UNA sola chiamata a `try_acquire_manual_backup_lock`
        nella funzione (no branch di retry).
        """
        src_path = Path(backups_service.__file__).resolve()
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        fn = _find_function(tree, "trigger_manual_backup")
        assert fn is not None

        try_acquire_calls = 0
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                func = node.func
                name = (
                    getattr(func, "id", None)
                    or getattr(func, "attr", None)
                )
                if name == "try_acquire_manual_backup_lock":
                    try_acquire_calls += 1

        assert try_acquire_calls == 1, (
            f"Atteso 1 try_acquire_manual_backup_lock in "
            f"trigger_manual_backup; trovati {try_acquire_calls}. "
            f"Branch di retry reintrodotto = TOCTOU."
        )
