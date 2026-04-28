"""Smoke test minimo per scripts/intermediate_run_report.py.

Non e' un mini-framework: verifica che lo script:
  - sia importabile senza side-effect runtime (no DB connect a import time)
  - esponga `report(run_id)` callable
  - su run_id inesistente ritorni exit code 2 senza crashare
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _load_script_module():
    """Carica scripts/intermediate_run_report.py come modulo."""
    backend_root = Path(__file__).resolve().parents[3]
    script_path = backend_root / "scripts" / "intermediate_run_report.py"
    assert script_path.exists(), f"Script non trovato: {script_path}"

    spec = importlib.util.spec_from_file_location(
        "intermediate_run_report_smoke", script_path,
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["intermediate_run_report_smoke"] = module
    spec.loader.exec_module(module)
    return module


def test_script_imports_without_side_effects():
    mod = _load_script_module()
    assert callable(mod.report)
    assert callable(mod.main)


def test_report_returns_2_for_unknown_run_id(capsys):
    mod = _load_script_module()
    with patch.object(mod, "_fetch_run", return_value=None):
        rc = mod.report("non-esistente")
    assert rc == 2
    err = capsys.readouterr().err
    assert "non trovata" in err
