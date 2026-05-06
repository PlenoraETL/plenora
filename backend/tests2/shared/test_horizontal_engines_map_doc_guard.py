"""Architectural lock for the horizontal engines map.

BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH.

Source-bound assertions on `docs/architecture/horizontal-engines.md`,
the concise canonical map of the six horizontal engine boundaries
the backend is converging onto. The page is the single-screen
index linked from the README and from
`docs/development/code-map.md`. Without these locks a future doc
edit could silently:

  - drop one of the six horizontal sections;
  - drop the "Do not import" guidance the Phase 2 BE_RULES rely
    on;
  - break the cross-link from the README so the page is
    discoverable;
  - drift the canonical import paths away from the current
    package layout.

Locks today:

  - the page exists with the canonical title and stream tag;
  - the six horizontal sections appear in canonical order with
    matching engine alignment tags
    (`AUTH_SECURITY_ENGINE`, `STORAGE_ARTIFACT_ENGINE`,
    `CONNECTION_ENGINE`, `OBSERVABILITY_ENGINE`,
    `DEPLOYMENT_RUNTIME_ENGINE`);
  - the "Do not import" section enumerates the four canonical
    anti-patterns (auth.auth re-export, config_db_service
    facade, unbounded stream join, mode="path" without
    inventory);
  - cross-links from README and from
    `docs/development/code-map.md` remain;
  - the page is ASCII-only.

Outside scope:
  - the per-module vertical map (locked by other code-map
    contracts);
  - the install-doc guards
    (`test_install_smoke_harness_doc_guard.py` etc.);
  - the Resource Engine pilot exit lock
    (`test_resource_engine_pilot_exit_criteria_lock.py`).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "architecture" / "horizontal-engines.md"
_README = _REPO_ROOT / "README.md"
_CODE_MAP = _REPO_ROOT / "docs" / "development" / "code-map.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


# =====================================================================
# Page existence + canonical title + stream tag
# =====================================================================


def test_horizontal_engines_doc_exists():
    assert _DOC.is_file(), (
        "docs/architecture/horizontal-engines.md must exist - "
        "DOCS-001 added it as the canonical horizontal-engines "
        "navigation map. If it has been deleted, the README + "
        "code-map cross-links must be removed in the same change."
    )


def test_doc_has_canonical_title_and_stream_tag(doc_text: str):
    assert doc_text.startswith("# Horizontal engines map\n"), (
        "horizontal-engines.md must start with the canonical "
        "title '# Horizontal engines map'. Renaming silently "
        "breaks the cross-link from the README."
    )
    assert "BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH" \
        in doc_text, (
        "horizontal-engines.md must keep the DOCS-001 stream "
        "tag so the audit trail back to the brief is preserved."
    )


# =====================================================================
# Six canonical horizontal sections + engine alignment tags
# =====================================================================


def test_doc_has_six_canonical_sections_in_order(doc_text: str):
    expected_in_order = (
        "## 1. Auth / Ownership / Disclosure",
        "## 2. Resource Engine + bounded reader + preview parser",
        "## 3. Storage / reconciliation",
        "## 4. DB config facade strangler + canonical `app/db/config/*`",
        "## 5. Health / readiness",
        "## 6. Install smoke path",
    )
    last = -1
    for header in expected_in_order:
        idx = doc_text.find(header)
        assert idx != -1, (
            f"horizontal-engines.md must contain section header "
            f"{header!r}. The six sections form the canonical "
            "horizontal map; dropping any of them lets the "
            "boundary discovery surface drift."
        )
        assert idx > last, (
            f"Section header {header!r} appears out of order. "
            "The six horizontal sections must appear 1..6 "
            "to match the engine-alignment ordering in BE_RULES."
        )
        last = idx


def test_doc_lists_engine_alignment_tags(doc_text: str):
    # Each horizontal section must declare the engine alignment
    # tag from BE_RULES so a reader can trace the section to the
    # rule.
    expected_tags = (
        "AUTH_SECURITY_ENGINE",
        "STORAGE_ARTIFACT_ENGINE",
        "CONNECTION_ENGINE",
        "OBSERVABILITY_ENGINE",
        "DEPLOYMENT_RUNTIME_ENGINE",
    )
    for tag in expected_tags:
        assert tag in doc_text, (
            f"horizontal-engines.md must reference engine "
            f"alignment tag {tag!r} (one of the canonical tags "
            "in BE_RULES 'Horizontal Engine Alignment'). Missing "
            "the tag means a section is not anchored to its rule."
        )


# =====================================================================
# Canonical entry points referenced
# =====================================================================


def test_doc_references_canonical_entry_points(doc_text: str):
    # The page must point at the canonical paths so the reader
    # does not have to guess. These are the load-bearing imports.
    expected_paths = (
        "backend/app/core/auth/middleware.py",
        "backend/app/core/auth/dependencies.py",
        "backend/app/core/resources/",
        "backend/app/core/resources/bounded_reader.py",
        "backend/app/db/config/",
        "backend/app/observability/health.py",
        "scripts/install.sh",
    )
    for path in expected_paths:
        assert path in doc_text, (
            f"horizontal-engines.md must reference canonical "
            f"path {path!r} in the relevant section. Missing the "
            "path forces the reader to grep the codebase, "
            "defeating the navigation purpose."
        )


# =====================================================================
# "Do not import" section
# =====================================================================


def test_doc_has_do_not_import_section(doc_text: str):
    assert "## Do not import" in doc_text, (
        "horizontal-engines.md must contain a '## Do not import' "
        "section. The four anti-patterns it enumerates are the "
        "binding part of the page; dropping the section turns "
        "the map into a passive index."
    )


def test_do_not_import_lists_four_canonical_guards(doc_text: str):
    do_not_idx = doc_text.find("## Do not import")
    assert do_not_idx != -1
    do_not_section = doc_text[do_not_idx:]
    expected_guards = (
        ("app.core.auth.auth",
         "Do-not-import section must call out the "
         "`app.core.auth.auth` re-export anti-pattern."),
        ("app.db.config_db_service",
         "Do-not-import section must call out the "
         "`app.db.config_db_service` facade anti-pattern."),
        ("unbounded stream",
         "Do-not-import section must call out unbounded stream "
         "joins (the `b\"\".join(stream['body'])` pattern)."),
        ("read_stream_bounded",
         "Do-not-import section must reference the canonical "
         "`read_stream_bounded` helper as the replacement for "
         "the unbounded stream pattern."),
        ("mode=\"path\"",
         "Do-not-import section must call out new `mode=\"path\"` "
         "callsites without inventory entry."),
    )
    for needle, message in expected_guards:
        assert needle in do_not_section, message


# =====================================================================
# Cross-links must remain
# =====================================================================


def test_readme_links_to_horizontal_engines_map():
    text = _README.read_text(encoding="utf-8")
    assert "horizontal-engines.md" in text, (
        "README.md must link to "
        "`docs/architecture/horizontal-engines.md` in the "
        "Architecture row. Without the link the page is not "
        "discoverable from the entry point of the repo."
    )


def test_code_map_links_to_horizontal_engines_map():
    text = _CODE_MAP.read_text(encoding="utf-8")
    assert "horizontal-engines.md" in text, (
        "docs/development/code-map.md must cross-link to "
        "`../architecture/horizontal-engines.md` in the "
        "'Contratti da non rompere' section. The vertical "
        "code-map and the horizontal engines map are "
        "complementary; the cross-link enforces that pairing."
    )


# =====================================================================
# ASCII-only
# =====================================================================


def test_doc_is_ascii_only(doc_text: str):
    non_ascii = [
        (i, ch) for i, ch in enumerate(doc_text)
        if ord(ch) > 127
    ]
    assert not non_ascii, (
        f"docs/architecture/horizontal-engines.md must be "
        f"ASCII-only (per scope rule). Found {len(non_ascii)} "
        f"non-ASCII char(s); first at offset {non_ascii[0][0]}: "
        f"U+{ord(non_ascii[0][1]):04X}."
    )
