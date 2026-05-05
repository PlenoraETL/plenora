"""Contract + visibility guard for the upload_intent lifecycle.

BE-GO-READY-STORAGE-ENGINE-023-UPLOAD-INTENT-READINESS-GUARD.

Locks three things that the audit (sec. 23 of
`.agent-temp/BE_STORAGE_ENGINE_AUDIT.md`) names as load-bearing:

  1. **Non-outbox docstring lock**: `record_upload_intent`'s
     docstring MUST contain the explicit "NOT a transactional
     outbox" callout + the disabled-state failure-mode list. The
     pattern has heuristic closure semantics, NOT outbox
     semantics; future contributors must not silently re-claim
     it as an outbox.
  2. **Per-operation visibility helper**: `count_pending_by_operation`
     exposes a `dict[str, int]` keyed on operation. Best-effort
     (returns `{}` on DB error). Locks the SQL contract
     (state='pending' filter, GROUP BY operation).
  3. **Readiness check extension**: `check_reconciliation_backlog`
     now (a) embeds the per-operation breakdown in its detail
     string and (b) honors the opt-in
     `STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD`
     setting (default 0 = disabled).

These tests are SOURCE-BOUND for the docstring lock + behavioral
for the runtime helpers. The check_reconciliation_backlog tests
patch the new boundary explicitly to avoid coupling to the
existing 015-readiness-check tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# =====================================================================
# 1. Non-outbox docstring lock
# =====================================================================


def test_record_upload_intent_docstring_disclaims_outbox_semantics():
    """The docstring MUST explicitly say "NOT a transactional outbox".
    A future contributor that quietly re-tags this pattern as an
    outbox would mislead callers into expecting at-least-once
    semantics that this implementation does not provide."""
    from app.shared.storage_reconciliation import record_upload_intent

    doc = record_upload_intent.__doc__ or ""
    assert "NOT a transactional outbox" in doc, (
        "record_upload_intent docstring must contain the literal "
        "'NOT a transactional outbox' disclaimer. Audit sec. 23 of "
        "BE_STORAGE_ENGINE_AUDIT.md is load-bearing on this point: "
        "the pattern is a self-clearing reconciliation log, not an "
        "outbox/saga commit."
    )


def test_record_upload_intent_docstring_names_disabled_state_failure_modes():
    """The docstring MUST name the three settings whose `<=0` value
    disables the closure path (= the row stays pending forever).
    Without this enumerated, ops won't know that toggling the
    reconciler flips the pattern from 'self-clearing' to 'silent
    accumulation'."""
    from app.shared.storage_reconciliation import record_upload_intent

    doc = record_upload_intent.__doc__ or ""
    required = (
        "STORAGE_RECONCILE_INTERVAL_SECONDS",
        "STORAGE_RECONCILE_UPLOAD_INTENT_GRACE_SECONDS",
        "STORAGE_RECONCILE_UPLOAD_INTENT_BATCH_SIZE",
    )
    missing = [s for s in required if s not in doc]
    assert not missing, (
        f"record_upload_intent docstring is missing the disabled-state "
        f"setting names: {missing}. Each setting that disables a piece "
        "of the closure path MUST be named so ops know what they "
        "lose by setting it to <=0."
    )


def test_record_upload_intent_docstring_points_at_visibility_helpers():
    """The docstring must reference the visibility helpers so
    operators can find them: `count_pending_by_operation` and the
    `reconciliation_backlog` readiness check."""
    from app.shared.storage_reconciliation import record_upload_intent

    doc = record_upload_intent.__doc__ or ""
    assert "count_pending_by_operation" in doc, (
        "Docstring must reference `count_pending_by_operation` so "
        "operators have a single name to grep for when investigating "
        "a stuck upload_intent backlog."
    )
    assert "reconciliation_backlog" in doc, (
        "Docstring must reference the `reconciliation_backlog` "
        "readiness check (the runtime surface that exposes the "
        "per-operation breakdown via `gather_readiness`)."
    )


# =====================================================================
# 2. count_pending_by_operation contract
# =====================================================================


def test_count_pending_by_operation_sql_filters_pending_groups_by_operation():
    """SQL must filter `state = 'pending'` and GROUP BY operation."""
    from app.shared import storage_reconciliation as recon

    captured = {}

    def _fake(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return []

    with patch("app.db.pg_connection.fetchall", _fake):
        recon.count_pending_by_operation()

    sql = captured["sql"]
    assert "state = 'pending'" in sql, sql
    assert "GROUP BY operation" in sql, sql
    # No bound parameters expected.
    assert captured["params"] == ()


def test_count_pending_by_operation_returns_dict_keyed_on_operation():
    """The helper returns a `dict[operation_name, count]`."""
    from app.shared import storage_reconciliation as recon

    rows = [
        {"operation": "delete", "count": 12},
        {"operation": "upload_intent", "count": 3},
    ]
    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): rows):
        out = recon.count_pending_by_operation()

    assert out == {"delete": 12, "upload_intent": 3}


def test_count_pending_by_operation_handles_tuple_rows():
    """Tolerates non-dict cursor flavors (mirror of
    `fetch_due_pending_deletes` tuple-row handling)."""
    from app.shared import storage_reconciliation as recon

    rows = [("delete", 5), ("upload_intent", 2)]
    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): rows):
        out = recon.count_pending_by_operation()

    assert out == {"delete": 5, "upload_intent": 2}


def test_count_pending_by_operation_swallows_db_error_returns_empty():
    """Best-effort: DB error -> warning + return `{}`. The
    readiness check MUST NOT crash on a transient DB issue."""
    from app.shared import storage_reconciliation as recon

    def _raise(sql, params=()):
        raise RuntimeError("DB unreachable")

    with patch("app.db.pg_connection.fetchall", _raise):
        out = recon.count_pending_by_operation()

    assert out == {}


def test_count_pending_by_operation_empty_table_returns_empty_dict():
    """Empty result set -> empty dict (NOT None, NOT [], NOT a
    dict with zero counts for known operations)."""
    from app.shared import storage_reconciliation as recon

    with patch("app.db.pg_connection.fetchall", lambda sql, params=(): []):
        out = recon.count_pending_by_operation()

    assert out == {}


# =====================================================================
# 3. check_reconciliation_backlog extension
# =====================================================================


def _stub_state_domain_counts(*, pending_total=0, lost_total=0):
    """Build a list of `ReconciliationStateCount`-shaped objects
    summing to the requested totals. Single domain="file_source"
    keeps the test focused on the totals; the state grouping is
    what the readiness check sums on."""
    from types import SimpleNamespace

    out = []
    if pending_total:
        out.append(
            SimpleNamespace(
                state="pending", domain="file_source", count=pending_total
            )
        )
    if lost_total:
        out.append(
            SimpleNamespace(
                state="lost", domain="file_source", count=lost_total
            )
        )
    return out


def test_backlog_check_detail_string_embeds_per_operation_breakdown():
    """When `count_pending_by_operation` returns non-empty, the
    detail string MUST embed a `(delete=N upload_intent=M)`
    breakdown so ops can distinguish W1 (delete) from W2
    (upload_intent) backlog at a glance."""
    from app.observability import health as h

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=_stub_state_domain_counts(pending_total=15, lost_total=0),
    ), patch(
        "app.shared.storage_reconciliation.count_pending_by_operation",
        return_value={"delete": 12, "upload_intent": 3},
    ):
        ok, detail = h.check_reconciliation_backlog()

    assert ok is True
    assert "pending=15" in detail
    assert "delete=12" in detail
    assert "upload_intent=3" in detail
    assert "lost=0" in detail


def test_backlog_check_detail_string_omits_breakdown_when_per_op_empty():
    """If `count_pending_by_operation` returns `{}` (DB read error
    or genuinely empty), the detail string falls back to the
    pre-023 shape (no breakdown). The check still runs."""
    from app.observability import health as h

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=_stub_state_domain_counts(pending_total=0, lost_total=0),
    ), patch(
        "app.shared.storage_reconciliation.count_pending_by_operation",
        return_value={},
    ):
        ok, detail = h.check_reconciliation_backlog()

    assert ok is True
    # Pre-023 shape: no parens with per-op breakdown.
    assert "pending=0 lost=0" in detail
    assert "(" not in detail


def test_backlog_check_default_threshold_is_zero_so_no_503_on_upload_intent():
    """Default `STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD`
    is 0 (= disabled). A growing upload_intent backlog must NOT
    trigger 503 by default — operators must opt in by setting the
    threshold > 0."""
    from app.config import Settings

    assert Settings.DEFAULTS["STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD"] == 0


def test_backlog_check_503_when_upload_intent_threshold_exceeded():
    """When the operator opts in (threshold > 0) and the
    upload_intent pending count exceeds it, the check returns
    `(False, ...)` with a dedicated `upload_intent=N over T`
    over-marker."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return {
                "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD": 1000,
                "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD": 10,
                "STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD": 5,
            }.get(key, default)

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=_stub_state_domain_counts(pending_total=20, lost_total=0),
    ), patch(
        "app.shared.storage_reconciliation.count_pending_by_operation",
        return_value={"delete": 12, "upload_intent": 8},
    ), patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_reconciliation_backlog()

    assert ok is False
    assert "upload_intent=8 over 5" in detail
    # Per-op breakdown still present in the trailing `(...)` block.
    assert "upload_intent=8" in detail
    assert "delete=12" in detail


def test_backlog_check_pending_global_threshold_still_fires():
    """The pre-023 behavior (`PENDING_503_THRESHOLD` global cap)
    is preserved verbatim. The new upload_intent threshold is
    additive."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return {
                "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD": 10,
                "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD": 10,
                "STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD": 0,
            }.get(key, default)

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=_stub_state_domain_counts(pending_total=15, lost_total=0),
    ), patch(
        "app.shared.storage_reconciliation.count_pending_by_operation",
        return_value={"delete": 15},
    ), patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_reconciliation_backlog()

    assert ok is False
    assert "pending=15 over 10" in detail


def test_backlog_check_swallows_per_op_helper_errors():
    """If `count_pending_by_operation` raises, the check still
    runs (the breakdown is just absent). The helper itself
    swallows DB errors and returns `{}`, so an exception
    here is pathological — but defense-in-depth."""
    from app.observability import health as h

    def _raise():
        raise RuntimeError("unexpected")

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=_stub_state_domain_counts(pending_total=0, lost_total=0),
    ), patch(
        "app.shared.storage_reconciliation.count_pending_by_operation",
        side_effect=_raise,
    ):
        ok, detail = h.check_reconciliation_backlog()

    assert ok is True
    # Pre-023 fallback shape, no breakdown.
    assert "pending=0 lost=0" in detail


def test_count_pending_by_operation_exported_in_storage_reconciliation_all():
    """The helper is part of the module's public surface."""
    import app.shared.storage_reconciliation as recon

    assert "count_pending_by_operation" in recon.__all__
