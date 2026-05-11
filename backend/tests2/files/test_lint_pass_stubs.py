"""
Wrapper pytest del linter `scripts/lint_pass_stubs.py`.

Mantiene il linter CI-friendly senza dipendere da pre-commit hook
(che non tutti hanno installato). Se il linter trova violazioni il
test fallisce con il report completo nella failure message.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_no_pass_stubs_in_engine_modules() -> None:
    backend_root = Path(__file__).resolve().parent.parent.parent
    linter = backend_root / "scripts" / "lint_pass_stubs.py"
    assert linter.exists(), f"linter mancante: {linter}"

    proc = subprocess.run(
        [sys.executable, str(linter)],
        capture_output=True,
        text=True,
        cwd=str(backend_root),
    )
    if proc.returncode != 0:
        out = proc.stdout + "\n" + proc.stderr
        raise AssertionError(
            f"lint_pass_stubs ha trovato violazioni "
            f"(exit={proc.returncode}):\n{out}"
        )
