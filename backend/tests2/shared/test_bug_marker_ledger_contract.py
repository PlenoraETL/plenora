"""Guard numbered backend bug/FIXME markers against undocumented growth."""

from __future__ import annotations

import re
from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.shared]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_REPO_ROOT = _BACKEND_ROOT.parent
_BUGS_LEDGER = _REPO_ROOT / "docs" / "development" / "bug-ledger.md"
_SCAN_ROOTS = (_BACKEND_ROOT / "app", _BACKEND_ROOT / "tests2")

_BUG_MARKER_RE = re.compile(
    r"\b(?:FIXME|TODO|BUG(?:\.md)?)\b[^\r\n#]{0,120}#(?P<id>\d+)"
)
_LEDGER_HEADING_RE = re.compile(r"^###\s+#(?P<id>\d+)\b", re.MULTILINE)


def _iter_python_files() -> list[Path]:
    paths: list[Path] = []
    for root in _SCAN_ROOTS:
        if root.exists():
            paths.extend(root.rglob("*.py"))
    return sorted(paths)


def _rel(path: Path) -> str:
    return path.relative_to(_REPO_ROOT).as_posix()


def _tracked_bug_ids() -> set[str]:
    text = _BUGS_LEDGER.read_text(encoding="utf-8")
    return set(_LEDGER_HEADING_RE.findall(text))


def _numbered_bug_markers() -> dict[str, list[str]]:
    markers: dict[str, list[str]] = {}
    for path in _iter_python_files():
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, start=1):
            for match in _BUG_MARKER_RE.finditer(line):
                bug_id = match.group("id")
                markers.setdefault(bug_id, []).append(f"{_rel(path)}:{line_no}")
    return markers


def test_numbered_bug_markers_are_documented_in_development_ledger() -> None:
    tracked = _tracked_bug_ids()
    markers = _numbered_bug_markers()
    untracked = {
        bug_id: locations
        for bug_id, locations in sorted(markers.items(), key=lambda item: int(item[0]))
        if bug_id not in tracked
    }

    assert not untracked, (
        "Numbered backend bug/FIXME markers must be tracked in "
        "docs/development/bug-ledger.md. "
        "Add a `### #N - ...` entry before introducing a new marker.\n"
        "Untracked markers:\n  - "
        + "\n  - ".join(
            f"#{bug_id}: {', '.join(locations)}"
            for bug_id, locations in untracked.items()
        )
    )


def test_bug_ledger_has_no_duplicate_marker_headings() -> None:
    text = _BUGS_LEDGER.read_text(encoding="utf-8")
    headings = _LEDGER_HEADING_RE.findall(text)
    duplicates = sorted(
        {bug_id for bug_id in headings if headings.count(bug_id) > 1},
        key=int,
    )

    assert not duplicates, (
        "docs/development/bug-ledger.md has duplicate marker headings: "
        + ", ".join(f"#{bug_id}" for bug_id in duplicates)
    )
