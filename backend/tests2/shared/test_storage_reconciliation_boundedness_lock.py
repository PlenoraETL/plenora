"""Architectural lock for storage_reconciliation boundedness.

BE-GO-READY-STORAGE-ENGINE-014-RECONCILIATION-BOUNDEDNESS-LOCK.

Source-bound guard suite that asserts the reconciliation backlog
cannot grow silently forever. The runtime mechanics already exist
(batch 005/006/008):

  - max attempts cap → terminal `lost` state via `mark_lost`;
  - exponential backoff capped at `2 ** 8`;
  - operational summary log every reconciler tick
    (`storage_reconciliation_summary`);
  - bounded retention prune every reconciler tick
    (`storage_reconciliation_prune_tick`).

The risk this lock addresses: a future refactor could silently drop
one of these wires (e.g., remove the prune call from
`_reconcile_storage`, or drop a `Settings.DEFAULTS` key) and the
reconciler would still function while quietly accumulating an
infinite backlog.

These tests are AST/text-level on the source files. They do NOT run
the reconciler at runtime; they assert that the source preserves
the boundedness contract.

Locked invariants
-----------------
1. `app/shared/storage_reconciliation.py` exposes the 4 boundedness
   public symbols (`mark_lost`, `compute_next_retry_at`,
   `count_active_by_state_domain`, `prune_terminal_older_than`).
2. The state machine has 4 states; 2 are terminal (`done`,
   `orphan_deleted`); 1 is sad-terminal (`lost`).
3. `compute_next_retry_at` caps the exponent (no unbounded backoff).
4. `count_active_by_state_domain` filters out terminals (the
   "active" definition).
5. `prune_terminal_older_than` only touches terminal states (never
   `pending` / `lost`).
6. `_reconcile_storage` in `workers/beat_scheduler.py`:
   a. invokes `mark_lost` (max-attempts terminal reachable);
   b. invokes `count_active_by_state_domain` (summary observability);
   c. invokes `prune_terminal_older_than` (bounded retention);
   d. honours `STORAGE_RECONCILE_MAX_ATTEMPTS`,
      `STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS`,
      `STORAGE_RECONCILE_PRUNE_BATCH_SIZE` from Settings.
7. `Settings.DEFAULTS` retains the 6 boundedness tunables with
   sensible default types (positive ints).
"""

from __future__ import annotations

from pathlib import Path

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent


def _read(rel: str) -> str:
    return (_BACKEND_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Public boundary surface
# ---------------------------------------------------------------------------


def test_storage_reconciliation_exposes_boundedness_symbols():
    """The 4 boundedness public symbols must remain importable. A
    future refactor that drops one of them would defeat the lock."""
    from app.shared.storage_reconciliation import (
        compute_next_retry_at,
        count_active_by_state_domain,
        mark_lost,
        prune_terminal_older_than,
    )
    assert callable(mark_lost)
    assert callable(compute_next_retry_at)
    assert callable(count_active_by_state_domain)
    assert callable(prune_terminal_older_than)


def test_storage_reconciliation_state_alphabet_locked():
    """The state alphabet must include the 4 known states. Adding
    new states is allowed (Literal extension); removing one breaks
    the contract — `lost` is the sad-terminal sink."""
    from app.shared.storage_reconciliation import ReconciliationState

    # ReconciliationState is a typing.Literal; introspect via __args__.
    args = getattr(ReconciliationState, "__args__", ())
    assert "pending" in args
    assert "done" in args
    assert "orphan_deleted" in args
    assert "lost" in args


# ---------------------------------------------------------------------------
# 2. Backoff cap (no unbounded growth)
# ---------------------------------------------------------------------------


def test_compute_next_retry_at_caps_the_exponent():
    """`compute_next_retry_at(attempts, base_seconds=...)` must cap
    the exponent — otherwise a row that has been failing for days
    would schedule retries hours / years apart, accumulating in
    `pending` state forever."""
    from app.shared.storage_reconciliation import compute_next_retry_at
    from datetime import datetime, timezone

    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    # If the exponent is uncapped, attempts=20 → 60 * 2**19 seconds
    # = ~31M seconds = ~1 year. A capped function returns the same
    # delta for attempts=9 and attempts=20 (the cap kicks in).
    out_9 = compute_next_retry_at(9, base_seconds=60, now=now)
    out_20 = compute_next_retry_at(20, base_seconds=60, now=now)
    assert out_9 == out_20, (
        "compute_next_retry_at exponent NOT capped: attempts=20 "
        f"yields {out_20 - now}, attempts=9 yields {out_9 - now}. "
        "The exponent must be bounded so backoff cannot grow without limit."
    )

    # And that capped delta must be a finite, sensible number of
    # seconds. With base=60, default cap should be <= 24h.
    delta = (out_20 - now).total_seconds()
    assert delta <= 24 * 3600, (
        f"compute_next_retry_at delta at the cap is {delta}s — "
        "should be <= 24h to keep retries bounded in time."
    )


# ---------------------------------------------------------------------------
# 3. State filters (active vs terminal)
# ---------------------------------------------------------------------------


def test_count_active_by_state_domain_sql_excludes_terminals():
    """The SQL must restrict the count to `pending` and `lost` —
    terminals (`done`, `orphan_deleted`) are audit trail and are
    not "active". Locking the SQL prevents silent inclusion of
    terminals (which would mask a real backlog under a swelling
    `done` count)."""
    src = _read("app/shared/storage_reconciliation.py")
    # Source-bound assertion on the SQL constant.
    assert "WHERE state IN ('pending', 'lost')" in src, (
        "count_active_by_state_domain SQL must filter `state IN "
        "('pending', 'lost')` so terminals don't dilute the active count."
    )


def test_prune_terminal_older_than_sql_only_touches_terminals():
    """The prune DELETE must restrict to `done` / `orphan_deleted` —
    pruning `pending` would lose work in flight; pruning `lost`
    would erase the forensic trail operators rely on."""
    src = _read("app/shared/storage_reconciliation.py")
    # The SQL is split across multiple lines with whitespace; check
    # for the canonical fragment.
    assert "state IN ('done', 'orphan_deleted')" in src, (
        "prune_terminal_older_than SQL must restrict to terminal "
        "states only. Pruning `pending` or `lost` would delete "
        "work-in-flight or forensic evidence."
    )
    # Bounded by LIMIT (PG doesn't support DELETE...LIMIT directly,
    # so the helper must use a sub-SELECT with LIMIT). Locate the
    # `_PRUNE_TERMINAL_SQL =` block and assert it contains LIMIT.
    if "_PRUNE_TERMINAL_SQL" in src:
        # Take a window of ~600 chars after the assignment to capture
        # the multi-line SQL.
        idx = src.index("_PRUNE_TERMINAL_SQL")
        window = src[idx : idx + 600]
        assert "LIMIT" in window, (
            "prune_terminal_older_than must enforce a per-call LIMIT to "
            "stay bounded; without it a single tick could lock the table."
        )


# ---------------------------------------------------------------------------
# 4. Beat hook wiring (boundedness must be invoked every tick)
# ---------------------------------------------------------------------------


def test_beat_scheduler_reconcile_invokes_mark_lost():
    """The reconciler dispatch must reach `mark_lost` when
    `attempts + 1 >= max_attempts`. Without this, a row would
    bounce between `pending` and `failed_attempt` indefinitely."""
    src = _read("workers/beat_scheduler.py")
    # The function definition exists.
    assert "def _reconcile_storage" in src
    # `mark_lost` is invoked from the reconciler — match either
    # `_recon.mark_lost(` or direct `mark_lost(` style.
    assert "mark_lost(" in src, (
        "_reconcile_storage must invoke mark_lost so attempts >= max "
        "moves entries to the terminal `lost` state."
    )
    # The max-attempts gate must reference the cap explicitly.
    assert "max_attempts" in src
    assert ">=" in src, (
        "_reconcile_storage must promote to lost when attempts >= cap."
    )


def test_beat_scheduler_reconcile_invokes_count_active_by_state_domain():
    """Operational summary must be emitted every reconciler tick.
    Drop it and ops loses the only signal of accumulating backlog."""
    src = _read("workers/beat_scheduler.py")
    assert "count_active_by_state_domain" in src, (
        "_reconcile_storage must invoke count_active_by_state_domain "
        "to emit the per-tick summary log "
        "`event=storage_reconciliation_summary`."
    )
    assert "storage_reconciliation_summary" in src, (
        "The summary log event name must be preserved — ops "
        "alerting filters on this exact event string."
    )


def test_beat_scheduler_reconcile_invokes_prune_terminal_older_than():
    """Bounded retention prune must run every tick. Drop it and the
    reconciliation log table grows without limit."""
    src = _read("workers/beat_scheduler.py")
    assert "prune_terminal_older_than" in src, (
        "_reconcile_storage must invoke prune_terminal_older_than "
        "to keep the table size bounded."
    )
    assert "storage_reconciliation_prune_tick" in src, (
        "The prune tick log event name must be preserved — ops "
        "uses it to verify retention is active."
    )


def test_beat_scheduler_reconcile_reads_boundedness_settings():
    """Settings keys for retention + max attempts must be read in
    the reconciler. A future refactor that hardcodes constants
    would silently break ops override."""
    src = _read("workers/beat_scheduler.py")
    for key in (
        "STORAGE_RECONCILE_MAX_ATTEMPTS",
        "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS",
        "STORAGE_RECONCILE_PRUNE_BATCH_SIZE",
    ):
        assert key in src, (
            f"Setting {key!r} must be read by _reconcile_storage so "
            "ops can tune the boundedness behavior at runtime."
        )


# ---------------------------------------------------------------------------
# 5. Settings.DEFAULTS — boundedness tunables surface
# ---------------------------------------------------------------------------


def test_settings_defaults_expose_boundedness_tunables():
    """All 6 boundedness tunables must be in `Settings.DEFAULTS`
    with positive int defaults (or > 0 as escape hatch). A drop
    here means env override stops working too."""
    from app.config import Settings

    expected = {
        "STORAGE_RECONCILE_INTERVAL_SECONDS": (int, lambda v: v > 0),
        "STORAGE_RECONCILE_BATCH_SIZE": (int, lambda v: v > 0),
        "STORAGE_RECONCILE_MAX_ATTEMPTS": (int, lambda v: v >= 1),
        "STORAGE_RECONCILE_BACKOFF_BASE_SECONDS": (int, lambda v: v > 0),
        "STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS": (int, lambda v: v > 0),
        "STORAGE_RECONCILE_PRUNE_BATCH_SIZE": (int, lambda v: v > 0),
    }
    for key, (expected_type, predicate) in expected.items():
        assert key in Settings.DEFAULTS, (
            f"Settings.DEFAULTS must expose {key!r} as a boundedness "
            "tunable."
        )
        value = Settings.DEFAULTS[key]
        assert isinstance(value, expected_type), (
            f"Settings.DEFAULTS[{key!r}] must be {expected_type.__name__}, "
            f"got {type(value).__name__}."
        )
        assert predicate(value), (
            f"Settings.DEFAULTS[{key!r}]={value!r} fails the boundedness "
            "predicate (must be a sensible positive number)."
        )


def test_storage_reconcile_max_attempts_default_is_finite():
    """`STORAGE_RECONCILE_MAX_ATTEMPTS` must be a small positive int
    (not 0, not negative, not absurdly large). 0 would defeat the
    `lost` terminal; >100 means an entry effectively never reaches
    `lost`."""
    from app.config import Settings

    cap = Settings.DEFAULTS["STORAGE_RECONCILE_MAX_ATTEMPTS"]
    assert 1 <= cap <= 50, (
        f"STORAGE_RECONCILE_MAX_ATTEMPTS={cap!r} is outside the "
        "sane operational range [1, 50]. The whole point of the "
        "cap is to make `lost` reachable in finite time."
    )


def test_storage_reconcile_terminal_retention_days_is_finite():
    """`STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS` must be > 0 by
    default — `<= 0` disables prune (escape hatch). The default
    must keep the prune ON."""
    from app.config import Settings

    retention = Settings.DEFAULTS["STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS"]
    assert retention > 0, (
        f"STORAGE_RECONCILE_TERMINAL_RETENTION_DAYS={retention!r} "
        "default must be > 0 so the prune is active out of the box. "
        "Ops can still set it <= 0 explicitly if they want unbounded."
    )


# ---------------------------------------------------------------------------
# 6. Observability event names locked (ops alerting depends on them)
# ---------------------------------------------------------------------------


def test_log_event_names_locked_in_source():
    """Ops dashboards/alerts filter on these exact event names. A
    rename here is a breaking change to the operational contract."""
    src_recon = _read("app/shared/storage_reconciliation.py")
    src_beat = _read("workers/beat_scheduler.py")

    # Per-row outcomes (boundary).
    assert "event=storage_reconciliation_mark_failed" in src_recon
    assert "event=storage_reconciliation_fetch_failed" in src_recon
    assert "event=storage_reconciliation_count_failed" in src_recon
    assert "event=storage_reconciliation_prune_failed" in src_recon

    # Per-tick aggregate (beat hook).
    assert "event=storage_reconciliation_beat_tick" in src_beat
    assert "event=storage_reconciliation_summary" in src_beat
    assert "event=storage_reconciliation_prune_tick" in src_beat
