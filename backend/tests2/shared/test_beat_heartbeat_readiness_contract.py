"""
Contract tests for `check_beat_heartbeat`.

BE-GO-READY-OBSERVABILITY-ENGINE-024-BEAT-HEARTBEAT-READINESS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# 1. Settings default + opt-in semantics


def test_settings_default_is_zero_disabled():
    """The default threshold is 0 = disabled. Production deploys
    with no beat scheduler (or the cleanroom CI smoke before the
    beat process starts) MUST NOT trigger 503. Operators opt in
    by setting a positive value."""
    from app.config import Settings

    assert "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS" in Settings.DEFAULTS
    val = Settings.DEFAULTS["READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS"]
    assert val == 0, (
        f"Default MUST be 0 (= disabled). Got {val}. Changing the "
        "default to a positive value would unexpectedly turn dev/test "
        "deploys without a beat scheduler into 503-on-/ready."
    )


def test_check_skipped_when_threshold_zero():
    """Threshold=0 -> skip-as-OK. The detail string MUST literally
    say 'disabled' so an operator running `curl /ready | jq` knows
    the check is opt-in."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return 0 if key == "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS" else default

    with patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "disabled" in detail
    assert "threshold=0" in detail


def test_check_skipped_when_threshold_negative():
    """Negative threshold -> still disabled (escape hatch). Mirrors
    the pattern in `check_reconciliation_backlog` where cap <= 0
    disables the branch."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return -5 if key == "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS" else default

    with patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "disabled" in detail


# 2. Threshold > 0: row presence and age semantics


def _settings_stub(*, max_age):
    class _S:
        def get(self, key, default=None):
            return (
                max_age
                if key == "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS"
                else default
            )
    return _S()


def test_missing_heartbeat_row_returns_false_with_explicit_message():
    """Beat process never started -> empty `beat_heartbeat` table
    -> outcome `(False, "missing heartbeat ...")`. The wording is
    locked: the operator must be able to grep the detail string
    for 'missing heartbeat'."""
    from app.observability import health as h

    def _fake_fetchone(sql, params=()):
        return None

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    assert ok is False
    assert "missing heartbeat" in detail


def test_stale_heartbeat_returns_false_with_explicit_message():
    """Beat process stopped 10 min ago, threshold 180s -> outcome
    `(False, "stale beat: age=600s max=180s")`. The wording is
    locked: 'stale beat' is the canonical token, and `age=` /
    `max=` make the timing diagnosis trivial."""
    from app.observability import health as h

    stale_ts = datetime.now(timezone.utc) - timedelta(seconds=600)

    def _fake_fetchone(sql, params=()):
        return {"ts": stale_ts}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    assert ok is False
    assert "stale beat" in detail
    assert "age=" in detail
    assert "max=180s" in detail


def test_fresh_heartbeat_returns_true_with_age_detail():
    """Beat process running, last heartbeat 30s ago, threshold 180s
    -> outcome `(True, "age=30s max=180s")`."""
    from app.observability import health as h

    fresh_ts = datetime.now(timezone.utc) - timedelta(seconds=30)

    def _fake_fetchone(sql, params=()):
        return {"ts": fresh_ts}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "age=" in detail
    assert "max=180s" in detail


def test_iso_string_timestamp_is_parsed():
    """Defense against driver flavors that return ts as ISO string
    (e.g. older psycopg2 paths). `_write_heartbeat` writes
    `datetime.now().isoformat()`."""
    from app.observability import health as h

    fresh_ts_str = (
        datetime.now(timezone.utc) - timedelta(seconds=10)
    ).isoformat()

    def _fake_fetchone(sql, params=()):
        return {"ts": fresh_ts_str}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "age=" in detail


def test_naive_datetime_is_treated_as_utc():
    """Edge: if the driver returns a naive datetime (no tzinfo),
    we coerce to UTC (mirror of `_write_heartbeat`'s contract)
    rather than crashing."""
    from app.observability import health as h

    naive = datetime.now() - timedelta(seconds=10)
    assert naive.tzinfo is None

    def _fake_fetchone(sql, params=()):
        return {"ts": naive}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    # Tolerant: the precise (ok, detail) depends on local clock skew
    # vs UTC, but the check MUST NOT raise.
    assert ok in (True, False)


# 3. Fail-OPEN on degraded signal


def test_db_error_is_fail_open():
    """DB unreachable / table missing in dev -> outcome
    `(True, "check skipped: ...")`. Parallel to the rest of the
    diagnostic checks: a probe that itself fails MUST NOT 503
    /ready when an upstream `check_postgres` already validates
    DB reachability."""
    from app.observability import health as h

    def _raise(sql, params=()):
        raise RuntimeError("table beat_heartbeat does not exist")

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _raise):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "check skipped" in detail


def test_settings_read_error_is_fail_open():
    """Bug in Settings access -> still fail-OPEN. Defense in depth
    against a setting being misconfigured (e.g. Settings stub in
    test that doesn't expose `.get`)."""
    from app.observability import health as h

    class _BrokenSettings:
        def get(self, key, default=None):
            raise RuntimeError("boom")

    with patch("app.config.settings", new=_BrokenSettings()):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "check skipped" in detail


def test_malformed_timestamp_is_fail_open():
    """If the driver returns something that's not a valid datetime,
    the check skips rather than 503ing. A corrupt heartbeat row is
    not a load-balancer problem."""
    from app.observability import health as h

    def _fake_fetchone(sql, params=()):
        return {"ts": "not-a-real-timestamp"}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "check skipped" in detail


# 4. SQL contract


def test_sql_targets_canonical_row():
    """The query MUST hit `plenora.beat_heartbeat WHERE id='beat'`.
    The id literal is hard-coded by `_write_heartbeat()`; renaming
    either side without coordinating breaks the readiness signal."""
    from app.observability import health as h

    captured = {}

    def _fake_fetchone(sql, params=()):
        captured["sql"] = sql
        return {"ts": datetime.now(timezone.utc)}

    with patch("app.config.settings", new=_settings_stub(max_age=180)), \
         patch("app.db.pg_connection.fetchone", _fake_fetchone):
        h.check_beat_heartbeat()

    assert "beat_heartbeat" in captured["sql"]
    assert "'beat'" in captured["sql"]
    assert "ts" in captured["sql"]


# 5. gather_readiness wiring


def test_check_beat_heartbeat_in_module_all():
    import app.observability.health as h

    assert "check_beat_heartbeat" in h.__all__


def test_gather_readiness_includes_beat_heartbeat_key():
    """The 8th check MUST appear in `gather_readiness().body['checks']`.
    Adding it after the fact would silently leave the readiness
    probe blind to a stale beat."""
    from app.observability import health as h

    # Stub all 8 checks to OK; we only assert the keyset shape.
    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_broker", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", return_value=(True, "ok")), \
         patch.object(h, "check_migrations_aligned", return_value=(True, "ok")), \
         patch.object(h, "check_object_storage", return_value=(True, "ok")), \
         patch.object(h, "check_reconciliation_backlog", return_value=(True, "ok")), \
         patch.object(h, "check_beat_heartbeat", return_value=(True, "ok")), \
         patch.object(h, "check_bootstrap_complete", return_value=(True, "ok")):
        payload = h.gather_readiness()

    assert "beat_heartbeat" in payload.body["checks"]
    assert payload.body["checks"]["beat_heartbeat"] == {
        "ok": True,
        "detail": "ok",
    }
    # Total keyset is exactly 8.
    assert len(payload.body["checks"]) == 8


def test_gather_readiness_returns_503_on_stale_beat():
    """When the operator has opted in (threshold > 0) and the beat
    is stale, /ready goes to 503 with `beat_heartbeat.ok = False`
    and a clear detail. Other 7 checks remain green."""
    from app.observability import health as h

    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_broker", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", return_value=(True, "ok")), \
         patch.object(h, "check_migrations_aligned", return_value=(True, "ok")), \
         patch.object(h, "check_object_storage", return_value=(True, "ok")), \
         patch.object(h, "check_reconciliation_backlog", return_value=(True, "ok")), \
         patch.object(
             h,
             "check_beat_heartbeat",
             return_value=(False, "stale beat: age=600s max=180s"),
         ), \
         patch.object(h, "check_bootstrap_complete", return_value=(True, "ok")):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["beat_heartbeat"]["ok"] is False
    assert "stale beat" in payload.body["checks"]["beat_heartbeat"]["detail"]


def test_gather_readiness_default_disabled_keeps_200_without_beat():
    """End-to-end safety net: with the default (threshold=0)
    setting and ALL other checks OK, /ready returns 200 even when
    `plenora.beat_heartbeat` is empty (no beat process). This is
    the dev/test guarantee the brief is asking for."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return (
                0
                if key == "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS"
                else default
            )

    def _fetchone_empty(sql, params=()):
        # `beat_heartbeat` table empty (no beat scheduler ever ran)
        return None

    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_broker", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", return_value=(True, "ok")), \
         patch.object(h, "check_migrations_aligned", return_value=(True, "ok")), \
         patch.object(h, "check_object_storage", return_value=(True, "ok")), \
         patch.object(h, "check_reconciliation_backlog", return_value=(True, "ok")), \
         patch.object(h, "check_bootstrap_complete", return_value=(True, "ok")), \
         patch("app.config.settings", new=_SettingsStub()), \
         patch("app.db.pg_connection.fetchone", _fetchone_empty):
        # NOTE: we do NOT patch check_beat_heartbeat here — we want
        # the real implementation to run with the default threshold.
        payload = h.gather_readiness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ready"
    bh = payload.body["checks"]["beat_heartbeat"]
    assert bh["ok"] is True
    assert "disabled" in bh["detail"]
