"""
Architectural lock for `record_pending` integration in result-blob deletes.

BE-GO-READY-STORAGE-ENGINE-007-RECONCILIATION-RESULT-BLOB-INTEGRATION.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests2.shared._ast_identifiers import (
    function_def_names,
    imports_module,
    imports_symbol_as,
    uses_attribute,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_CLEANUP = _BACKEND_ROOT / "app" / "core" / "results" / "cleanup.py"
_RESULTS_CLEANUP_REPOSITORY = (
    _BACKEND_ROOT / "app" / "core" / "results" / "cleanup_repository.py"
)
_STORAGE_RECONCILIATION = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "storage_reconciliation.py"
)


def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice_function(source: str, marker: str) -> str:
    """Return the lexical body of a top-level function whose `def` line
    matches `marker`. Stops at the next top-level `def `/`class `.

    Source-bound assertions only need the function body; this avoids
    pulling in unrelated module text and keeps the locks tight to the
    function under audit.
    """
    lines = source.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith(marker):
            start = i
            break
    if start is None:
        raise AssertionError(f"marker not found in source: {marker!r}")

    end = len(lines)
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if line.startswith("def ") or line.startswith("class "):
            end = j
            break
    return "\n".join(lines[start:end])


# 1-2. delete_result: record_pending precedes DELETE inside transaction


def test_delete_result_calls_record_pending_inside_transaction():
    source = _read_source(_RESULTS_CLEANUP)
    body = _slice_function(source, "def delete_result(")

    # RAW: statement/structural pattern, not a pure identifier
    assert "with transaction() as conn:" in body, (
        "delete_result must run inside a `with transaction()` block — "
        "record_pending and DELETE row must commit atomically."
    )
    assert imports_symbol_as(
        source, "app.core.resources", "storage_reconciliation", "reconciliation"
    ), (
        "delete_result must import record_pending from the typed "
        "boundary module."
    )
    assert uses_attribute(body, "reconciliation", "record_pending"), (
        "delete_result must call record_pending. Without it the W1 "
        "race window has no application-side trail."
    )
    # RAW: string literal value (kwarg value, not identifier)
    assert 'domain="result_blob"' in body, (
        "record_pending in delete_result must declare "
        'domain="result_blob" so the reconciler beat hook can route '
        "the entry."
    )
    # RAW: string literal value (kwarg value, not identifier)
    assert 'operation="delete"' in body, (
        "record_pending in delete_result must declare "
        'operation="delete" — upload_intent is not the W1 shape.'
    )


def test_delete_result_record_pending_precedes_delete_statement():
    body = _slice_function(
        _read_source(_RESULTS_CLEANUP), "def delete_result("
    )

    record_pending_pos = body.find("reconciliation.record_pending(")
    delete_pos = body.find("DELETE FROM results WHERE id = %s")

    assert record_pending_pos != -1, (
        "delete_result missing record_pending call — see "
        "test_delete_result_calls_record_pending_inside_transaction."
    )
    assert delete_pos != -1, (
        "delete_result missing the canonical DELETE FROM results "
        "WHERE id = %s statement."
    )
    assert record_pending_pos < delete_pos, (
        "record_pending MUST precede DELETE FROM results lexically. "
        "Inverting the order would let a transaction death between "
        "DELETE and INSERT into storage_reconciliation_log produce "
        "an orphan blob with no trail."
    )


# 3-4. delete_results_for_project: bulk path same invariants


def test_delete_results_for_project_calls_record_pending_inside_transaction():
    source = _read_source(_RESULTS_CLEANUP)
    body = _slice_function(source, "def delete_results_for_project(")

    # RAW: statement/structural pattern, not a pure identifier
    assert "with transaction() as conn:" in body, (
        "delete_results_for_project must run inside a `with "
        "transaction()` block."
    )
    assert imports_symbol_as(
        source, "app.core.resources", "storage_reconciliation", "reconciliation"
    ), (
        "delete_results_for_project must import record_pending from "
        "the typed boundary module."
    )
    assert uses_attribute(body, "reconciliation", "record_pending"), (
        "delete_results_for_project must call record_pending per row "
        "with a non-NULL storage_key."
    )
    # RAW: string literal value (kwarg value, not identifier)
    assert 'domain="result_blob"' in body, (
        "record_pending in delete_results_for_project must declare "
        'domain="result_blob".'
    )
    # RAW: string literal value (kwarg value, not identifier)
    assert 'operation="delete"' in body, (
        "record_pending in delete_results_for_project must declare "
        'operation="delete".'
    )


def test_delete_results_for_project_records_pending_after_delete_returning():
    body = _slice_function(
        _read_source(_RESULTS_CLEANUP),
        "def delete_results_for_project(",
    )

    record_pending_pos = body.find("reconciliation.record_pending(")
    delete_pos = body.find("DELETE FROM results WHERE project_id = %s")
    returning_pos = body.find("RETURNING id, storage_key")

    assert record_pending_pos != -1, (
        "delete_results_for_project missing record_pending call."
    )
    assert delete_pos != -1, (
        "delete_results_for_project missing the canonical bulk "
        "DELETE FROM results WHERE project_id = %s statement."
    )
    assert returning_pos != -1, (
        "delete_results_for_project must keep DELETE ... RETURNING "
        "so pending cleanup entries are created for the exact deleted rows."
    )
    assert delete_pos < record_pending_pos, (
        "record_pending MUST precede the bulk DELETE statement — "
        "preserving the W1 invariant on the bulk path."
    )


# 5. compensating_delete + mark_done/failed wired post-commit


def test_delete_result_paths_wire_compensating_delete_and_mark_outcomes():
    source = _read_source(_RESULTS_CLEANUP)

    for fn in ("def delete_result(", "def delete_results_for_project("):
        body = _slice_function(source, fn)

        assert imports_symbol_as(
            source, "app.infrastructure.storage", "compensation", "storage_compensation"
        ), (
            f"{fn} must import compensating_delete from the unified "
            "helper module."
        )
        assert uses_attribute(body, "storage_compensation", "compensating_delete"), (
            f"{fn} must invoke compensating_delete after the commit."
        )
        # RAW: string literal value (kwarg value, not identifier)
        assert 'domain="result_blob"' in body, (
            f"{fn} compensating_delete must use domain='result_blob' "
            "so logs and reconciler routing are consistent with "
            "record_pending."
        )
        assert uses_attribute(body, "reconciliation", "mark_done"), (
            f"{fn} must call mark_done on outcome ok/skipped — "
            "without it pending rows accumulate forever even on the "
            "happy path."
        )
        assert uses_attribute(body, "reconciliation", "mark_failed_attempt"), (
            f"{fn} must call mark_failed_attempt on outcome failed — "
            "without it the reconciler beat hook never re-tries."
        )


# 6. Boundary surface preserved


def test_results_cleanup_owns_delete_surface():
    source = _read_source(_RESULTS_CLEANUP)

    assert "delete_result" in function_def_names(source)
    assert "delete_results_for_project" in function_def_names(source)


def test_results_cleanup_uses_repository_for_pg_connection():
    cleanup_source = _read_source(_RESULTS_CLEANUP)
    repository_source = _read_source(_RESULTS_CLEANUP_REPOSITORY)

    assert not imports_module(cleanup_source, "app.db.pg_connection")
    assert imports_module(repository_source, "app.db.pg_connection")


def test_storage_reconciliation_exposes_record_pending_surface():
    source = _read_source(_STORAGE_RECONCILIATION)

    for symbol in (
        '"record_pending"',
        '"mark_done"',
        '"mark_failed_attempt"',
    ):
        assert symbol in source, (
            f"app/core/resources/storage_reconciliation.py __all__ missing "
            f"{symbol}. Removing it would silently break "
            "delete_result / delete_results_for_project imports."
        )

    assert "record_pending" in function_def_names(source), (
        "record_pending function definition missing — boundary "
        "contract relied on by W1 callsites."
    )
