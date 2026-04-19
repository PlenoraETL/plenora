"""Meta-test: anti-pattern espliciti da evitare in tests2."""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_no_lock_the_bug_antipatterns():
    """La suite non deve contenere pattern testuali che congelano bug noti.

    Questo rende self-enforcing la policy documentata in tests2/README.md:
    i lock/regression test devono proteggere il contratto desiderato,
    non fotografare un comportamento sbagliato temporaneo.
    """
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    bad_phrases = [
        "_NON_blocking",
        "lock del bug",
        "potenzialmente sorprendente",
        "semantica corrente",
        "bug osservato ma non fixato",
        "_is_broken_but",
        "_bug_observed",
        "non bloccante",
    ]

    for file in root.rglob("test_*.py"):
        if file == Path(__file__).resolve():
            continue
        content = file.read_text(encoding="utf-8")
        for phrase in bad_phrases:
            if phrase in content:
                rel = file.relative_to(root)
                violations.append(f"{rel}: '{phrase}'")

    assert not violations, "Antipattern lock-the-bug trovati:\n" + "\n".join(violations)


def test_skip_markers_always_include_reason():
    """I test skip devono avere sempre un reason esplicito e non vuoto."""
    root = Path(__file__).resolve().parents[1]
    violations: list[str] = []

    for file in root.rglob("test_*.py"):
        if file == Path(__file__).resolve():
            continue
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), start=1):
            if "@pytest.mark.skip" not in line:
                continue
            if "reason=" not in line or 'reason=""' in line or "reason=''" in line:
                rel = file.relative_to(root)
                violations.append(f"{rel}:{lineno}: {line.strip()}")

    assert not violations, "Skip senza reason esplicito trovati:\n" + "\n".join(violations)
