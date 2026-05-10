"""Production dead-code guard.

This test keeps Vulture useful after the cleanup pass: known framework and
dynamic-dispatch false positives are allowlisted, while new unapproved
dead-code findings fail loudly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from backend.tests2.shared.dead_code_allowlist import is_allowed_vulture_finding


REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_vulture() -> list[str]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "vulture",
            "backend/app",
            "backend/workers",
            "backend/scripts",
            "--min-confidence",
            "60",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def test_vulture_reports_only_allowlisted_production_false_positives() -> None:
    findings = _run_vulture()
    unexpected = [
        line for line in findings
        if ("unused function" in line or "unused method" in line)
        and not is_allowed_vulture_finding(line)
    ]

    assert unexpected == [], (
        "Vulture found unapproved production dead-code candidates. "
        "Remove the code or document the structural false positive in "
        "backend/tests2/shared/dead_code_allowlist.py:\n"
        + "\n".join(unexpected[:80])
    )
