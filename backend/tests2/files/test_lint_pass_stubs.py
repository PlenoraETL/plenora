"""
Wrapper pytest del linter `scripts/lint_pass_stubs.py`.

Mantiene il linter CI-friendly senza dipendere da pre-commit hook
(che non tutti hanno installato). Se il linter trova violazioni il
test fallisce con il report completo nella failure message.
"""

from __future__ import annotations

import subprocess
import sys
import importlib.util
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


def test_lint_pass_stubs_flags_async_with_pass(tmp_path: Path) -> None:
    backend_root = Path(__file__).resolve().parent.parent.parent
    linter_path = backend_root / "scripts" / "lint_pass_stubs.py"
    spec = importlib.util.spec_from_file_location("lint_pass_stubs", linter_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    target = tmp_path / "sample.py"
    target.write_text(
        "async def f(lock):\n"
        "    async with lock:\n"
        "        pass\n",
        encoding="utf-8",
    )

    violations = module._check_file(target, tmp_path)
    assert violations
    assert "with lock" in str(violations[0])
