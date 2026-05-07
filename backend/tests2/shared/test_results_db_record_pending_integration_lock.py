"""Architectural lock for `record_pending` integration in result-blob deletes.

BE-GO-READY-STORAGE-ENGINE-007-RECONCILIATION-RESULT-BLOB-INTEGRATION.

Source-bound guard suite that asserts the W1-race coverage shipped
in batch 021 (`delete_result` / `delete_results_for_project` →
`record_pending` inside the same transaction as `DELETE FROM
results`) cannot silently regress.

The runtime mechanics already exist (proven by the 8 contract tests
in `tests2/shared/test_results_db_delete_reconciliation_contract.py`):

  - both delete paths call `record_pending(cur, domain="result_blob",
    operation="delete", ...)` BEFORE the DELETE statement;
  - both run inside a single `transaction()` block — commit advances
    the pending row and the DELETE atomically;
  - post-commit `compensating_delete` outcome flips the entry to
    `mark_done` / `mark_failed_attempt`.

The risk this lock addresses: a future refactor could silently drop
the W1 wire (e.g., remove the `record_pending` call but leave the
post-commit `compensating_delete`) and the reconciler would lose
the pre-commit trail. Without the trail, a process death between DB
commit and storage delete leaves an orphan blob with no
application-side audit.

These tests are AST/text-level on the source files. They do NOT run
the runtime; they assert that the source preserves the W1 contract.

Locked invariants
-----------------
1. `app/db/results_db_service.py:delete_result` calls
   `record_pending` with `domain="result_blob"`, `operation="delete"`,
   inside a `with transaction()` block.
2. `record_pending` precedes the `DELETE FROM results WHERE id = %s`
   statement (lexical order).
3. `app/db/results_db_service.py:delete_results_for_project` calls
   `record_pending` with `domain="result_blob"`, `operation="delete"`,
   inside a `with transaction()` block.
4. `record_pending` precedes the `DELETE FROM results WHERE
   project_id = %s` statement (lexical order).
5. Post-commit, both paths invoke `compensating_delete` with
   `domain="result_blob"` and dispatch to `mark_done` /
   `mark_failed_attempt`.
6. Public boundary surface in `app/shared/storage_reconciliation.py`
   continues to expose `record_pending`, `mark_done`,
   `mark_failed_attempt`.

Out of scope (locked separately or tracked as residuals)
--------------------------------------------------------
- W2 race (`_cleanup_uploaded_result_blob` in `app/utils/result_store.py`):
  upload-intent pattern, deferred to batch 008
  (`STORAGE-ENGINE-008-UPLOAD-INTENT-PATTERN`).
- Multi-layer file_source upload rollback callback in
  `app/services/file_service.py:_delete_blob`: also W2 (post-rollback
  cleanup of just-uploaded blob), same batch 008 closure path.
- Intermediate full_artifact orphans
  (`app/core/intermediate/db.py:_delete_run` cascade-deletes
  `node_meta.artifact_uri` rows but performs NO storage cleanup
  today): different domain, different W1 shape (no compensating
  delete exists yet), tracked as new residual `R-007.1` in
  `BE_STORAGE_ENGINE_AUDIT.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent
_RESULTS_DB_SERVICE = _BACKEND_ROOT / "app" / "db" / "results_db_service.py"
_STORAGE_RECONCILIATION = (
    _BACKEND_ROOT / "app" / "shared" / "storage_reconciliation.py"
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
    body = _slice_function(
        _read_source(_RESULTS_DB_SERVICE), "def delete_result("
    )

    assert "with transaction() as conn:" in body, (
        "delete_result must run inside a `with transaction()` block — "
        "record_pending and DELETE row must commit atomically."
    )
    assert "from app.shared.storage_reconciliation import (" in body, (
        "delete_result must import record_pending from the typed "
        "boundary module."
    )
    assert "record_pending(" in body, (
        "delete_result must call record_pending. Without it the W1 "
        "race window has no application-side trail."
    )
    assert 'domain="result_blob"' in body, (
        "record_pending in delete_result must declare "
        'domain="result_blob" so the reconciler beat hook can route '
        "the entry."
    )
    assert 'operation="delete"' in body, (
        "record_pending in delete_result must declare "
        'operation="delete" — upload_intent is not the W1 shape.'
    )


def test_delete_result_record_pending_precedes_delete_statement():
    body = _slice_function(
        _read_source(_RESULTS_DB_SERVICE), "def delete_result("
    )

    record_pending_pos = body.find("record_pending(")
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
    body = _slice_function(
        _read_source(_RESULTS_DB_SERVICE),
        "def delete_results_for_project(",
    )

    assert "with transaction() as conn:" in body, (
        "delete_results_for_project must run inside a `with "
        "transaction()` block."
    )
    assert "from app.shared.storage_reconciliation import (" in body, (
        "delete_results_for_project must import record_pending from "
        "the typed boundary module."
    )
    assert "record_pending(" in body, (
        "delete_results_for_project must call record_pending per row "
        "with a non-NULL storage_key."
    )
    assert 'domain="result_blob"' in body, (
        "record_pending in delete_results_for_project must declare "
        'domain="result_blob".'
    )
    assert 'operation="delete"' in body, (
        "record_pending in delete_results_for_project must declare "
        'operation="delete".'
    )


def test_delete_results_for_project_record_pending_precedes_bulk_delete():
    body = _slice_function(
        _read_source(_RESULTS_DB_SERVICE),
        "def delete_results_for_project(",
    )

    record_pending_pos = body.find("record_pending(")
    delete_pos = body.find("DELETE FROM results WHERE project_id = %s")

    assert record_pending_pos != -1, (
        "delete_results_for_project missing record_pending call."
    )
    assert delete_pos != -1, (
        "delete_results_for_project missing the canonical bulk "
        "DELETE FROM results WHERE project_id = %s statement."
    )
    assert record_pending_pos < delete_pos, (
        "record_pending MUST precede the bulk DELETE statement — "
        "preserving the W1 invariant on the bulk path."
    )


# 5. compensating_delete + mark_done/failed wired post-commit


def test_delete_result_paths_wire_compensating_delete_and_mark_outcomes():
    source = _read_source(_RESULTS_DB_SERVICE)

    for fn in ("def delete_result(", "def delete_results_for_project("):
        body = _slice_function(source, fn)

        assert (
            "from app.shared.storage_compensation import compensating_delete"
            in body
        ), (
            f"{fn} must import compensating_delete from the unified "
            "helper module."
        )
        assert "compensating_delete(" in body, (
            f"{fn} must invoke compensating_delete after the commit."
        )
        assert 'domain="result_blob"' in body, (
            f"{fn} compensating_delete must use domain='result_blob' "
            "so logs and reconciler routing are consistent with "
            "record_pending."
        )
        assert "mark_done" in body, (
            f"{fn} must call mark_done on outcome ok/skipped — "
            "without it pending rows accumulate forever even on the "
            "happy path."
        )
        assert "mark_failed_attempt" in body, (
            f"{fn} must call mark_failed_attempt on outcome failed — "
            "without it the reconciler beat hook never re-tries."
        )


# 6. Boundary surface preserved


def test_storage_reconciliation_exposes_record_pending_surface():
    source = _read_source(_STORAGE_RECONCILIATION)

    for symbol in (
        '"record_pending"',
        '"mark_done"',
        '"mark_failed_attempt"',
    ):
        assert symbol in source, (
            f"app/shared/storage_reconciliation.py __all__ missing "
            f"{symbol}. Removing it would silently break "
            "delete_result / delete_results_for_project imports."
        )

    assert "def record_pending(" in source, (
        "record_pending function definition missing — boundary "
        "contract relied on by W1 callsites."
    )
