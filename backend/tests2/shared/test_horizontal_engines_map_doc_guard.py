"""
Architectural lock for the horizontal engines map.

BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.shared,
    pytest.mark.contract,
    pytest.mark.architecture,
    pytest.mark.docs_guard,
    pytest.mark.cleanroom_excluded,
]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_DOC = _REPO_ROOT / "docs" / "architecture" / "horizontal-engines.md"
_README = _REPO_ROOT / "README.md"
_CODE_MAP = _REPO_ROOT / "docs" / "development" / "code-map.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    return _DOC.read_text(encoding="utf-8")


# Page existence + canonical title + stream tag


def test_horizontal_engines_doc_exists():
    assert _DOC.is_file(), (
        "docs/architecture/horizontal-engines.md must exist - "
        "DOCS-001 added it as the canonical horizontal-engines "
        "navigation map. If it has been deleted, the README + "
        "code-map cross-links must be removed in the same change."
    )


def test_doc_has_canonical_title_and_stream_tag(doc_text: str):
    assert doc_text.startswith("# Horizontal Engines\n"), (
        "horizontal-engines.md must start with the canonical "
        "title '# Horizontal Engines'. Renaming silently "
        "breaks the cross-link from the README."
    )
    assert "BE-GO-READY-DOCS-001-CODEMAP-ARCHITECTURE-REFRESH" \
        in doc_text, (
        "horizontal-engines.md must keep the DOCS-001 stream "
        "tag so the audit trail back to the brief is preserved."
    )


# Canonical horizontal sections


def test_doc_has_canonical_sections_in_order(doc_text: str):
    expected_in_order = (
        "## 1. Auth / Security Engine",
        "## 2. Connections Engine",
        "## 3. Execution Engine",
        "## 4. Transformation Engine",
        "## 5. Resource / Storage Engine",
        "## 6. Persistence / DB Engine",
        "## 7. Observability / Install Engine",
    )
    last = -1
    for header in expected_in_order:
        idx = doc_text.find(header)
        assert idx != -1, (
            f"horizontal-engines.md must contain section header "
            f"{header!r}. The sections form the canonical "
            "horizontal map; dropping any of them lets the "
            "boundary discovery surface drift."
        )
        assert idx > last, (
            f"Section header {header!r} appears out of order. "
            "The horizontal sections must appear in canonical order."
        )
        last = idx


# Canonical entry points referenced


def test_doc_references_canonical_entry_points(doc_text: str):
    # The page must point at the canonical paths so the reader
    # does not have to guess. These are the load-bearing imports.
    expected_paths = (
        "app/core/auth/auth_middleware.py",
        "app/core/auth/dependencies.py",
        "app/core/connections/rest_sink.py",
        "app/core/connections/arcgis_sql_helpers.py",
        "app/core/connections/arcgis_publish_modes.py",
        "app/shared/execution/schema_types.py",
        "app/shared/execution/canvas_types.py",
        "app/shared/execution/clock.py",
        "app/core/scheduler/job_dispatcher.py",
        "app/core/scheduler/run_log_store.py",
        "app/core/imdf/publish_dispatcher.py",
        "app/core/dataframes/*",
        "app/core/dataframes/ports.py",
        "app/core/dataframes/default_loader.py",
        "app/core/dataframes/frame_io.py",
        "app/core/dataframes/geodata.py",
        "app/core/dataframes/geospatial_io.py",
        "app/core/dataframes/load_dispatch.py",
        "app/core/dataframes/parallel_ops.py",
        "app/core/dataframes/schema.py",
        "app/core/dataframes/values.py",
        "app/core/resources/*",
        "app/core/results/output_file_cleanup.py",
        "app/core/files/cleanup.py",
        "app/core/scheduler/run_archive_store.py",
        "app/core/eseguibili/output_store.py",
        "app/core/imdf/bundle_store.py",
        "app/core/intermediate/retention.py",
        "app/shared/resources/file_cleanup.py",
        "app/db/query_result_persistence.py",
        "app/db/migrations_runner.py",
        "app/db/sql_functions.py",
        "app/observability/health.py",
        "app/core/task_runtime/probes.py",
        "scripts/install.sh",
    )
    for path in expected_paths:
        assert path in doc_text, (
            f"horizontal-engines.md must reference canonical "
            f"path {path!r} in the relevant section. Missing the "
            "path forces the reader to grep the codebase, "
            "defeating the navigation purpose."
        )


# "Do not import" section


def test_doc_has_do_not_import_section(doc_text: str):
    assert "## Do not import" in doc_text, (
        "horizontal-engines.md must contain a '## Do not import' "
        "section. The four anti-patterns it enumerates are the "
        "binding part of the page; dropping the section turns "
        "the map into a passive index."
    )


def test_do_not_import_lists_canonical_guards(doc_text: str):
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
        ("app.core.connections.rest_sink",
         "Do-not-import section must point production callers at "
         "the neutral REST sink boundary."),
        ("app.core.dataframes.load_dispatch",
         "Do-not-import section must point file-source parser dispatch "
         "at the dataframe dispatch boundary."),
        ("app.core.dataframes.ports",
         "Do-not-import section must point dataframe contracts "
         "at the pandas-neutral ports boundary."),
        ("app.core.dataframes.default_loader",
         "Do-not-import section must point concrete file-source loader "
         "behavior at the dataframe loader boundary."),
        ("app.core.dataframes.frame_io",
         "Do-not-import section must point dataframe path/bytes I/O "
         "at the dataframe I/O boundary."),
        ("app.core.dataframes.geospatial_io",
         "Do-not-import section must point geospatial dataframe I/O "
         "at the dataframe geospatial I/O boundary."),
        ("app.core.dataframes.schema",
         "Do-not-import section must point pandas-backed column inference "
         "at the dataframe schema boundary."),
        ("app.core.dataframes.values",
         "Do-not-import section must point scalar null checks and "
         "DataFrame probes at the dataframe values boundary."),
        ("app.core.dataframes.parallel_ops",
         "Do-not-import section must point split/merge/index guard "
         "behavior at the dataframe parallel-ops boundary."),
        ("app.core.features.arcgis_sql_helpers",
         "Do-not-import section must call out the legacy ArcGIS SQL "
         "features helper path."),
        ("app.core.features.arcgis_publish_modes",
         "Do-not-import section must call out the legacy ArcGIS publish "
         "features helper path."),
        ("app.core.scheduler.job_dispatcher",
         "Do-not-import section must point production callers at "
         "the scheduler dispatch boundary."),
        ("app.core.scheduler.run_log_store",
         "Do-not-import section must point scheduler catalog/view code "
         "at the scheduler run log boundary."),
        ("app.core.scheduler.run_archive_store",
         "Do-not-import section must point scheduler archive lookup "
         "at the archive-store boundary."),
        ("app.core.imdf.publish_dispatcher",
         "Do-not-import section must point production callers at "
         "the IMDF dispatch boundary."),
        ("app.core.task_runtime.probes",
         "Do-not-import section must point production callers at "
         "the task-runtime worker probe boundary."),
        ("app.core.files.cleanup",
         "Do-not-import section must point group file-source cleanup "
         "at the storage-aware lifecycle boundary."),
        ("app.core.results.output_file_cleanup",
         "Do-not-import section must point project result output cleanup "
         "at the result cleanup boundary."),
        ("app.core.eseguibili.output_store",
         "Do-not-import section must point eseguibili output access "
         "at the task-scoped output boundary."),
        ("app.core.imdf.bundle_store",
         "Do-not-import section must point IMDF bundle deletion "
         "at the lifecycle boundary."),
    )
    for needle, message in expected_guards:
        assert needle in do_not_section, message


# Cross-links must remain


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


# ASCII-only


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
