"""
Cost-boundedness lock for `gather_readiness()` checks.

BE-GO-READY-OBSERVABILITY-ENGINE-025-READINESS-COST-BOUNDEDNESS-AUDIT.
"""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

from tests2.shared._source_scan import python_code_only as _python_code_only


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_HEALTH_PY = (
    _REPO_ROOT / "backend" / "app" / "observability" / "health.py"
)
_TASK_RUNTIME_PROBES_PY = (
    _REPO_ROOT / "backend" / "app" / "core" / "task_runtime" / "probes.py"
)

_READINESS_CHECKS_OK = {
    "check_postgres": (True, "ok"),
    "check_redis": (True, "ok"),
    "check_pool_headroom": (True, "ok"),
    "check_migrations_aligned": (True, "ok"),
    "check_object_storage": (True, "ok"),
    "check_reconciliation_backlog": (True, "ok"),
    "check_beat_heartbeat": (True, "ok"),
    "check_bootstrap_complete": (True, "ok"),
}

_READINESS_EXPECTED_ORDER = [
    "postgres",
    "redis",
    "pool_headroom",
    "migrations",
    "object_storage",
    "reconciliation_backlog",
    "beat_heartbeat",
    "bootstrap_complete",
]


def _patch_readiness_checks(h, **overrides):
    outcomes = _READINESS_CHECKS_OK.copy()
    outcomes.update(overrides)
    stack = ExitStack()
    for check_name, outcome in outcomes.items():
        stack.enter_context(patch.object(h, check_name, return_value=outcome))
    return stack


# 1. Default-disabled posture for opt-in checks
#
# A new readiness check that defaults to ENABLED can unexpectedly
# turn dev/test/CLI deploys into 503-on-/ready. Both opt-in checks
# (beat heartbeat + upload_intent backlog 503 threshold) MUST stay
# default-0 = disabled; flipping the default to a positive value
# is a behavioural regression.


def test_beat_heartbeat_threshold_default_is_zero():
    """Locked by audit §19.5: dev/test guarantee."""
    from app.config import Settings

    val = Settings.DEFAULTS["READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS"]
    assert val == 0, (
        f"READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS default MUST be 0 "
        f"(= opt-in disabled). Got {val!r}. A positive default would "
        "make any deploy without a running beat scheduler 503-on-/ready, "
        "breaking dev/test/CLI environments and the cleanroom CI "
        "smoke (which spawns beat after /ready)."
    )


def test_upload_intent_threshold_default_is_zero():
    from app.config import Settings

    val = Settings.DEFAULTS["STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD"]
    assert val == 0, (
        f"STORAGE_RECONCILE_BACKLOG_UPLOAD_INTENT_503_THRESHOLD default "
        f"MUST be 0 (= opt-in disabled). Got {val!r}. A positive default "
        "would surface the W2-race upload_intent backlog as a 503 "
        "trigger before operators have observed their own baseline."
    )


def test_pool_headroom_check_does_not_have_default_threshold_setting():
    """`check_pool_headroom` does not consult Settings — it inspects
    the in-process pool. Confirm no setting was silently introduced
    that would change the default behaviour."""
    from app.config import Settings

    # The ONLY pool-related setting is the pool max size itself.
    # Nothing in the readiness check namespace.
    pool_thresholds = [
        k for k in Settings.DEFAULTS
        if "POOL_HEADROOM" in k or "READINESS_POOL" in k
    ]
    assert pool_thresholds == [], (
        f"Unexpected pool-headroom readiness setting introduced: "
        f"{pool_thresholds}. The check is in-memory and should remain "
        "settings-free."
    )


# 2. Capability-disabled / threshold-disabled skip paths produce 200


def test_object_storage_skip_path_returns_true():
    """STORAGE_POLICY=db / capability disabled MUST return
    `(True, "skipped: capability disabled")` and NOT trigger 503."""
    from app.observability import health as h

    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=False,
    ):
        ok, detail = h.check_object_storage()

    assert ok is True
    assert "skipped" in detail
    assert "capability disabled" in detail


def test_object_storage_capability_race_returns_true():
    """Race teorica: capability ON al check, OFF al call -> skip-as-OK
    (no 503 on a transient capability flip)."""
    from app.observability import health as h
    from app.infrastructure.storage.cloud_backends import ObjectStorageDisabledError

    def _raise_disabled():
        raise ObjectStorageDisabledError("capability_disabled_race")

    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=True,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        side_effect=_raise_disabled,
    ):
        ok, detail = h.check_object_storage()

    assert ok is True
    assert "skipped" in detail
    assert "race" in detail


def test_beat_heartbeat_default_threshold_skip_returns_true():
    """Default threshold=0 -> skip-as-OK with explicit "disabled"
    detail. Locks the dev/test guarantee."""
    from app.observability import health as h

    class _SettingsStub:
        def get(self, key, default=None):
            return (
                0
                if key == "READINESS_BEAT_HEARTBEAT_MAX_AGE_SECONDS"
                else default
            )

    with patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_beat_heartbeat()

    assert ok is True
    assert "disabled" in detail


# 3. Keyset count + ordering lock
#
# The 8-key shape is asserted by
# `tests2/observability/test_health_module_contract.py::
# test_gather_readiness_returns_200_when_all_8_ok`. We add a
# complementary lock that fails LOUDLY if a 9th check is added
# without going through the audit (sec. §19.4 of
# BE_INSTALLATION_AUDIT.md).


def test_gather_readiness_keyset_is_exactly_8_in_canonical_order():
    """Adding a 9th check MUST be paired with an audit update.
    This guard prevents silent expansion of the readiness surface.
    The 8 keys must also keep their stable insertion order.

    If you are adding a 9th check, ALSO update:
      - `.agent-temp/BE_INSTALLATION_AUDIT.md` §19 (cost classification);
      - `tests2/observability/test_health_module_contract.py`
        (rename the keyset test to reflect the new count);
      - this assertion."""
    from app.observability import health as h

    # Stub all current checks to OK so the test does not depend on
    # local state. Any new check added without updating this stub
    # list would fail the assertion below.
    with _patch_readiness_checks(h):
        payload = h.gather_readiness()

    assert len(payload.body["checks"]) == 8, (
        f"Readiness check count regressed: got "
        f"{sorted(payload.body['checks'].keys())}. If you are adding a "
        "9th check, see the audit guidance in the docstring above."
    )

    actual_order = list(payload.body["checks"].keys())
    assert actual_order == _READINESS_EXPECTED_ORDER, (
        f"Readiness key order changed: expected {_READINESS_EXPECTED_ORDER}, "
        f"got {actual_order}. Reorder the gather_readiness() body or "
        "update this assertion + the audit document explicitly."
    )


# 4. Source-bound: no direct network call without timeout/skip path
#
# The cost-boundedness invariant: any check that does network I/O
# MUST either:
#   - bound it with an explicit timeout kwarg (e.g.
#     `socket_connect_timeout`, `timeout=`);
#   - OR go through `cloud_backends` (which is owned by config +
#     has a capability-disabled skip path).
# A new check that imports `requests`, `urllib.request`,
# `http.client`, or `httpx` directly is the canonical regression
# (an HTTP GET with no timeout default-blocks for ~75s on Linux,
# which would gate /ready behind a remote service's failure mode).


_BANNED_NETWORK_IMPORTS = (
    # Top-level imports that bypass the cloud_backends boundary.
    # The list is intentionally exhaustive — a check should not
    # need any of these directly.
    "import requests",
    "from requests",
    "import httpx",
    "from httpx",
    "import urllib.request",
    "from urllib.request",
    "import http.client",
    "from http.client",
)


def test_health_module_has_no_unbounded_network_imports():
    """Source-bound: `app/observability/health.py` MUST NOT import
    a generic HTTP client. Any check that hits a remote service
    must go through `cloud_backends` (capability gate + bounded
    SDK timeouts) or `redis.from_url(..., socket_connect_timeout=N)`.

    The Redis check uses `redis` directly (allowed) but with an
    explicit `socket_connect_timeout=2` (asserted below)."""
    text = _HEALTH_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)

    offenders = [n for n in _BANNED_NETWORK_IMPORTS if n in code_only]
    assert not offenders, (
        f"`app/observability/health.py` imports banned network "
        f"client(s): {offenders}. A new readiness check that hits a "
        "remote service MUST either go through `cloud_backends` "
        "(capability gate + bounded SDK timeouts) or use a Redis-"
        "style explicit `socket_connect_timeout=N` argument. "
        "Direct `requests` / `urllib` / `httpx` imports are unbounded "
        "by default (~75s Linux SYN timeout) and gate /ready behind "
        "a remote service's failure mode."
    )


def test_redis_check_uses_socket_connect_timeout():
    """Source-bound lock on the only direct Redis network call:
    `socket_connect_timeout=` MUST be present. Removing it would
    change /ready's worst-case latency from ~2s to the kernel's
    default SYN-retransmit window (~75s on Linux)."""
    text = _TASK_RUNTIME_PROBES_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)
    assert "socket_connect_timeout" in code_only, (
        "ping_redis_broker() must use `redis.from_url(..., "
        "socket_connect_timeout=N)`. Without it, an unreachable "
        "Redis (e.g. Redis pod restarting in k8s) would block "
        "/ready for ~75s on the kernel SYN retransmit timeout."
    )


def test_object_storage_check_goes_through_cloud_backends_capability_gate():
    """Source-bound lock: `check_object_storage` consults
    `object_storage_enabled()` BEFORE attempting any network call.
    Removing the gate would turn a `STORAGE_POLICY=db` deploy into
    a /ready 503 the moment the SDK times out trying to reach a
    bucket that was never configured."""
    text = _HEALTH_PY.read_text(encoding="utf-8")
    assert "object_storage_enabled" in text, (
        "check_object_storage() must consult the capability gate "
        "BEFORE attempting `list_objects`. Removing the gate would "
        "make `STORAGE_POLICY=db` deploys 503 on /ready waiting for "
        "an SDK timeout."
    )


# 5. Defense-in-depth: heavy checks declare fail-OPEN posture
#
# `check_pool_headroom` / `check_migrations_aligned` /
# `check_reconciliation_backlog` / `check_beat_heartbeat` are
# diagnostic checks that fall back to (True, "check skipped: ...")
# on internal failure. The fall-back wording is a load-bearing
# contract: an operator running `curl /ready | jq` must be able to
# distinguish "skipped" (degraded signal) from "ok" (success).


def test_diagnostic_checks_use_skipped_wording_on_internal_error():
    """All diagnostic check functions MUST contain the literal
    `"skipped"` string somewhere in their bodies (the fail-OPEN
    fallback). Source-bound for stability."""
    text = _HEALTH_PY.read_text(encoding="utf-8")

    # We assert the bodies contain "skipped" enough times: each
    # diagnostic check has at least one fall-back. The current
    # count is 9 (across the 5 diagnostic checks: pool_headroom,
    # migrations_aligned, object_storage import-error+capability+race,
    # reconciliation_backlog import+settings+counts,
    # beat_heartbeat settings+db+ts+disabled).
    skipped_count = text.count("skipped")
    assert skipped_count >= 8, (
        f"Diagnostic fail-OPEN wording 'skipped' appears only "
        f"{skipped_count} times. Each diagnostic check should have at "
        "least one fail-OPEN fallback that says 'skipped'. Removing "
        "fall-backs without explicit audit update is a regression."
    )


def test_check_postgres_remains_fail_closed():
    """`check_postgres` is the ONE check that should NEVER be
    fail-OPEN: if PG is unreachable, /ready cannot reasonably claim
    readiness (every other DB-backed check would lie).

    Post OPS-002, the probe goes through `get_connection` (so the
    `SET LOCAL statement_timeout` can run on the same transaction
    as `SELECT 1`); patch that entry point to simulate unreachable
    PG."""
    from app.observability import health as h

    def _raise():
        raise RuntimeError("PG unreachable")

    with patch("app.db.pg_connection.get_connection", _raise):
        ok, detail = h.check_postgres()

    assert ok is False, (
        "check_postgres MUST be fail-CLOSED on DB error. A fail-OPEN "
        "PG check would mask all downstream DB-backed checks: every "
        "fail-OPEN diagnostic falls back to a 'skipped' pass, and a "
        "broken PG would silently turn /ready green."
    )
    assert "RuntimeError" in detail


def test_check_redis_remains_fail_closed():
    """`check_redis` is the second mandatory dependency. Fail-CLOSED
    by design: Celery requires Redis."""
    from app.observability import health as h

    class _BrokenRedis:
        def ping(self):
            raise RuntimeError("Redis unreachable")

    with patch("redis.from_url", return_value=_BrokenRedis()):
        ok, detail = h.check_redis()

    assert ok is False
    assert "RuntimeError" in detail


# 6. OPS-001: process-only liveness + Redis PING-level timeout
#
# BE-GO-READY-OPS-001-READINESS-COST-BOUNDEDNESS adds three locks:
#   (a) liveness must NEVER call dependency/readiness checks
#       (restart-safe process-only latency budget);
#   (b) Redis PING must be bounded at the OPERATION level
#       (`socket_timeout`), not only at the TCP handshake level
#       (`socket_connect_timeout`);
#   (c) the Redis timeout is configurable via
#       `READINESS_REDIS_TIMEOUT_SECONDS` so operators can tune
#       per-deploy without source edits.


def test_gather_liveness_keyset_is_process_only():
    """Liveness keyset MUST be exactly `{process}`. Adding
    dependency probes to liveness would make /health capable of
    restarting the app on transient dependency lag.

    A k8s liveness probe failure kills the pod; a readiness probe
    failure only removes it from the load-balancer pool. Deep PG
    SELECT 1 and Redis PING therefore belong to /ready."""
    from app.observability import health as h

    payload = h.gather_liveness()

    keys = list(payload.body["checks"].keys())
    assert keys == ["process"], (
        f"gather_liveness keyset changed: expected ['process'], got "
        f"{keys}. Liveness must NOT include dependency or readiness "
        "checks. A liveness probe failure kills the pod; /ready is "
        "the endpoint for PG, Redis, and diagnostic checks."
    )


def test_gather_liveness_does_not_call_dependency_or_readiness_checks():
    """If liveness called PG, Redis, or any diagnostic readiness check
    they would fire here (we patch them to raise)."""
    from app.observability import health as h

    def _explode():
        raise AssertionError(
            "Dependency/readiness check called from gather_liveness - "
            "this would make /health restart-sensitive."
        )

    with patch.object(h, "check_postgres", side_effect=_explode), \
         patch.object(h, "check_redis", side_effect=_explode), \
         patch.object(h, "check_pool_headroom", side_effect=_explode), \
         patch.object(h, "check_migrations_aligned", side_effect=_explode), \
         patch.object(h, "check_object_storage", side_effect=_explode), \
         patch.object(h, "check_reconciliation_backlog", side_effect=_explode), \
         patch.object(h, "check_beat_heartbeat", side_effect=_explode), \
         patch.object(h, "check_bootstrap_complete", side_effect=_explode):
        payload = h.gather_liveness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ok"
    assert list(payload.body["checks"].keys()) == ["process"]


def test_redis_check_uses_socket_operation_timeout():
    """Source-bound: `check_redis` must pass BOTH
    `socket_connect_timeout` (TCP handshake bound) AND
    `socket_timeout` (PING-operation bound). Without the
    operation timeout, a Redis that accepts the TCP handshake but
    stalls before responding to PING would block /ready
    until the kernel TCP keepalive fires (~minutes)."""
    text = _TASK_RUNTIME_PROBES_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)
    assert "socket_timeout" in code_only, (
        "ping_redis_broker() must pass `socket_timeout=` in addition to "
        "`socket_connect_timeout=`. Without it, a Redis that accepts "
        "the connection but stalls before responding to PING would "
        "block /ready beyond the connect timeout window."
    )


def test_redis_timeout_setting_default_is_two_seconds():
    """`READINESS_REDIS_TIMEOUT_SECONDS` default = 2. Bounds both
    the TCP handshake and the PING. Operators may tune per-deploy
    (k8s tight SLO -> 1; noisy network -> 5). Cap <= 0 falls back
    to the hardcoded 2 (intentionally NOT disable-able: /ready
    without a bound would be a footgun)."""
    from app.config import Settings

    val = Settings.DEFAULTS["READINESS_REDIS_TIMEOUT_SECONDS"]
    assert val == 2, (
        f"READINESS_REDIS_TIMEOUT_SECONDS default must be 2 seconds. "
        f"Got {val!r}. Tightening below 2s risks false positives on "
        "warm-restart Redis pods; raising above 5s starts to drift "
        "outside the load-balancer-friendly latency budget for "
        "/ready."
    )


def test_check_redis_falls_back_to_two_seconds_on_invalid_setting():
    """`check_redis` must NOT honour <=0 or non-int settings as
    "no timeout" — that would break the cost-boundedness invariant
    via a typo'd env. The fallback is a hardcoded 2."""
    from app.observability import health as h

    captured: dict = {}

    class _StubRedis:
        @classmethod
        def from_url(cls, url, **kwargs):
            captured.update(kwargs)
            return cls()

        def ping(self):
            return True

    class _SettingsStub:
        CELERY_BROKER_URL = "redis://stub"

        def get(self, key, default=None):
            if key == "READINESS_REDIS_TIMEOUT_SECONDS":
                return 0  # invalid: must fall back to 2
            return default

    with patch("app.config.settings", new=_SettingsStub()), \
         patch("redis.from_url", _StubRedis.from_url):
        ok, _ = h.check_redis()

    assert ok is True
    assert captured.get("socket_connect_timeout") == 2, (
        f"socket_connect_timeout fallback must be 2s on invalid "
        f"setting; got {captured.get('socket_connect_timeout')!r}."
    )
    assert captured.get("socket_timeout") == 2, (
        f"socket_timeout fallback must be 2s on invalid setting; "
        f"got {captured.get('socket_timeout')!r}."
    )


def test_health_module_has_no_top_level_heavy_imports():
    """Source-bound: `app/observability/health.py` MUST keep heavy
    deps (redis SDK, boto3, app.bootstrap, migrations runner,
    cloud_backends, storage_reconciliation) under the `def
    check_*` lazy-import pattern. Top-level imports of these would
    pay startup cost on every `import app.observability.health`
    (e.g. CLI tools, alembic, conftest, the eager-import
    `app.main` chain) — defeating the lazy-leaf design.
    """
    text = _HEALTH_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)

    # Walk top-level lines only (no function bodies). A top-level
    # import has zero leading whitespace; a lazy import inside a
    # function is indented. We scan the source line-by-line and
    # examine only zero-indent lines.
    banned_top = {
        "import redis",
        "from redis",
        "import boto3",
        "from boto3",
        "from app.bootstrap",
        "import app.bootstrap",
        "from app.db.migrations_runner",
        "from app.infrastructure.storage.cloud_backends",
        "from app.core.resources.storage_reconciliation",
        "from app.db.pg_connection",
    }
    offenders: list[str] = []
    for raw_line in code_only.splitlines():
        if not raw_line:
            continue
        if raw_line[0] in (" ", "\t"):
            continue  # indented -> inside function/class -> lazy
        for needle in banned_top:
            if raw_line.startswith(needle):
                offenders.append(raw_line.strip())
    assert not offenders, (
        f"`app/observability/health.py` has top-level heavy "
        f"imports: {offenders}. Move each into the `def check_*` "
        "body (lazy import) so `import app.observability.health` "
        "stays cheap. The lazy-leaf design is documented in the "
        "module docstring (\"Boundary\" section)."
    )


# 7. OPS-002: PG + object-storage readiness timeout bounds
#
# BE-GO-READY-OPS-002-READINESS-PG-OBJECTSTORE-TIMEOUTS adds two
# bounds matching the OPS-001 Redis pattern:
#   (a) `check_postgres` runs `SELECT 1` inside a transaction with
#       `SET LOCAL statement_timeout = Nms`. Without this a PG that
#       accepts the TCP connection but stalls in execution (lock,
#       backend exhaustion, query plan stuck) would block /ready
#       beyond the kernel TCP keepalive.
#   (b) `check_object_storage` calls a one-shot bounded boto3 client
#       via `storage.readiness_probe(timeout_seconds=...)` instead
#       of the runtime `list_objects` (which uses the boto3 default
#       60s connect + 60s read with 3 retries -> ~360s worst case).
#       The readiness client passes `Config(connect_timeout,
#       read_timeout, retries={"max_attempts": 1})`.
#
# Both timeouts are configurable; both fall back to a hardcoded
# default when the setting is <= 0 or non-int (mirroring the
# Redis "footgun" guard).


def test_pg_timeout_setting_default_is_two_seconds():
    """`READINESS_PG_TIMEOUT_SECONDS` default = 2. Matches Redis. Cap
    <= 0 falls back to the hardcoded 2 (intentionally NOT
    disable-able)."""
    from app.config import Settings

    val = Settings.DEFAULTS["READINESS_PG_TIMEOUT_SECONDS"]
    assert val == 2, (
        f"READINESS_PG_TIMEOUT_SECONDS default must be 2 seconds. "
        f"Got {val!r}. Tightening below 2s risks false positives on "
        "warm-restart PG; raising above 5s starts to drift outside "
        "the load-balancer-friendly latency budget for /ready."
    )


def test_object_storage_timeout_setting_default_is_three_seconds():
    """`READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS` default = 3. One
    second above PG/Redis to absorb regional S3 latency. Cap <= 0
    falls back to 3."""
    from app.config import Settings

    val = Settings.DEFAULTS["READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS"]
    assert val == 3, (
        f"READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS default must be "
        f"3 seconds. Got {val!r}. The +1s over the PG/Redis 2s "
        "absorbs regional S3 latency without drifting outside the "
        "load-balancer-friendly /ready window."
    )


def test_check_postgres_uses_statement_timeout():
    """`check_postgres` MUST `SET LOCAL statement_timeout = Nms` on
    its transaction before running `SELECT 1` plus `current_schema()`.
    Without this a stalled PG would block /ready beyond the TCP keepalive,
    and a bad search_path could silently point runtime SQL at the wrong
    schema."""
    from app.observability import health as h

    captured: list[str] = []

    class _StubCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def execute(self, sql, params=None):
            captured.append(sql)

        def fetchone(self):
            return {"v": 1, "current_schema": "plenora"}

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    with patch("app.db.pg_connection.get_connection", return_value=_StubConn()), \
         patch("app.db.pg_connection.put_connection", lambda c: None):
        ok, detail = h.check_postgres()

    assert ok is True
    assert detail == "ok schema=plenora"
    assert any("SET LOCAL statement_timeout" in s for s in captured), (
        f"check_postgres must issue `SET LOCAL statement_timeout = Nms` "
        f"on the probe transaction. Captured statements: {captured}. "
        "Without this bound a stalled PG (locks, exhausted backends) "
        "would block /ready beyond the kernel TCP keepalive."
    )
    assert any("SELECT 1" in s for s in captured), (
        f"check_postgres must still issue `SELECT 1`. Captured: "
        f"{captured}."
    )
    assert any("current_schema()" in s for s in captured), (
        f"check_postgres must validate the active DB schema/search_path. "
        f"Captured: {captured}."
    )


def test_check_postgres_fails_on_schema_mismatch():
    """`check_postgres` must fail readiness when pool search_path does not
    resolve to the configured DB_SCHEMA.
    """
    from app.observability import health as h

    class _StubCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def execute(self, sql, params=None):
            pass

        def fetchone(self):
            return {"v": 1, "current_schema": "public"}

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    class _SettingsStub:
        def get(self, key, default=None):
            if key == "DB_SCHEMA":
                return "plenora"
            return default

    with patch("app.db.pg_connection.get_connection", return_value=_StubConn()), \
         patch("app.db.pg_connection.put_connection", lambda c: None), \
         patch("app.config.settings", new=_SettingsStub()):
        ok, detail = h.check_postgres()

    assert ok is False
    assert "schema mismatch" in detail
    assert "public" in detail
    assert "plenora" in detail


def test_check_postgres_falls_back_to_two_seconds_on_invalid_setting():
    """`check_postgres` must NOT honour <=0 or non-int settings as
    "no timeout" — that would break the cost-boundedness invariant."""
    from app.observability import health as h

    captured: list[str] = []

    class _StubCursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return None

        def execute(self, sql, params=None):
            captured.append(sql)

        def fetchone(self):
            return {"v": 1, "current_schema": "plenora"}

    class _StubConn:
        def cursor(self):
            return _StubCursor()

        def commit(self):
            pass

        def rollback(self):
            pass

    class _SettingsStub:
        def get(self, key, default=None):
            if key == "READINESS_PG_TIMEOUT_SECONDS":
                return 0  # invalid: must fall back to 2
            return default

    with patch("app.db.pg_connection.get_connection", return_value=_StubConn()), \
         patch("app.db.pg_connection.put_connection", lambda c: None), \
         patch("app.config.settings", new=_SettingsStub()):
        ok, _ = h.check_postgres()

    assert ok is True
    set_local = next(
        (s for s in captured if "SET LOCAL statement_timeout" in s),
        None,
    )
    assert set_local is not None
    # 2 seconds -> 2000ms
    assert "2000" in set_local, (
        f"statement_timeout fallback must be 2000ms on invalid setting; "
        f"got {set_local!r}."
    )


def test_check_object_storage_uses_bounded_probe_method():
    """`check_object_storage` MUST go through
    `storage.readiness_probe(timeout_seconds=...)`, NOT the runtime
    `list_objects`. The runtime path uses boto3 defaults (60s connect
    + 60s read with 3 retries) which can total ~360s on a hung
    endpoint."""
    from app.observability import health as h

    captured: dict = {}

    class _StubStorage:
        def list_objects(self, prefix):
            captured["list_objects_called"] = True
            return []

        def readiness_probe(self, timeout_seconds):
            captured["readiness_probe_timeout"] = timeout_seconds

    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=True,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        return_value=_StubStorage(),
    ):
        ok, detail = h.check_object_storage()

    assert ok is True
    assert detail == "ok"
    assert "readiness_probe_timeout" in captured, (
        "check_object_storage must call `storage.readiness_probe(...)`, "
        f"not list_objects. Captured: {captured}."
    )
    assert "list_objects_called" not in captured, (
        "check_object_storage must NOT call `storage.list_objects(...)` "
        "on the readiness path: that uses unbounded boto3 defaults."
    )


def test_check_object_storage_falls_back_to_three_seconds_on_invalid_setting():
    from app.observability import health as h

    captured: dict = {}

    class _StubStorage:
        def readiness_probe(self, timeout_seconds):
            captured["timeout"] = timeout_seconds

    class _SettingsStub:
        def get(self, key, default=None):
            if key == "READINESS_OBJECT_STORAGE_TIMEOUT_SECONDS":
                return -1  # invalid: must fall back to 3
            return default

    with patch(
        "app.infrastructure.storage.cloud_backends.object_storage_enabled",
        return_value=True,
    ), patch(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        return_value=_StubStorage(),
    ), patch(
        "app.config.settings", new=_SettingsStub()
    ):
        ok, _ = h.check_object_storage()

    assert ok is True
    assert captured.get("timeout") == 3, (
        f"object_storage timeout fallback must be 3s on invalid "
        f"setting; got {captured.get('timeout')!r}."
    )


def test_object_storage_readiness_probe_builds_bounded_boto3_client():
    """`AwsS3ObjectStorage.readiness_probe` MUST build a one-shot
    boto3 client with `Config(connect_timeout, read_timeout,
    retries={"max_attempts": 1})`. The retry disable is critical:
    boto3's default 3-attempt exponential retry would multiply the
    timeout window by ~3x on a hung endpoint."""
    from app.infrastructure.storage.cloud_backends import AwsS3ObjectStorage

    captured: dict = {}

    class _StubClient:
        def list_objects_v2(self, **kwargs):
            captured["list_kwargs"] = kwargs
            return {}

    def _stub_boto3_client(service, **kwargs):
        captured["service"] = service
        captured["client_kwargs"] = kwargs
        return _StubClient()

    storage = AwsS3ObjectStorage(bucket="probe-bucket", region="eu-south-1")
    with patch("boto3.client", side_effect=_stub_boto3_client):
        storage.readiness_probe(timeout_seconds=2)

    assert captured["service"] == "s3"
    cfg = captured["client_kwargs"].get("config")
    assert cfg is not None, (
        "readiness_probe must pass a botocore Config to boto3.client; "
        f"got kwargs={captured['client_kwargs']!r}."
    )
    assert cfg.connect_timeout == 2, (
        f"readiness Config.connect_timeout must equal the requested "
        f"timeout_seconds; got {cfg.connect_timeout!r}."
    )
    assert cfg.read_timeout == 2, (
        f"readiness Config.read_timeout must equal the requested "
        f"timeout_seconds; got {cfg.read_timeout!r}."
    )
    # botocore stores retries config as a dict on the Config object.
    retries = getattr(cfg, "retries", None)
    assert retries is not None, "readiness Config must declare retries"
    assert retries.get("max_attempts") == 1, (
        f"readiness Config.retries.max_attempts must be 1 to disable "
        f"the 3-attempt exponential default; got "
        f"{retries.get('max_attempts')!r}."
    )
    # The probe must scope to the dedicated prefix and bound the page
    # size so even an enabled bucket does not page through everything.
    list_kwargs = captured.get("list_kwargs", {})
    assert list_kwargs.get("Bucket") == "probe-bucket"
    assert list_kwargs.get("Prefix") == ".plenora-readiness-probe/"
    assert list_kwargs.get("MaxKeys") == 1


def test_minio_readiness_probe_preserves_endpoint_and_addressing_style():
    """MinIO subclass MUST keep `endpoint_url`, explicit credentials,
    and `addressing_style=path` on the readiness client (vanilla
    MinIO does NOT support virtual-hosted style). Dropping any of
    these would break the probe on every MinIO deploy."""
    from app.infrastructure.storage.cloud_backends import MinioObjectStorage

    captured: dict = {}

    class _StubClient:
        def list_objects_v2(self, **kwargs):
            return {}

    def _stub_boto3_client(service, **kwargs):
        captured["client_kwargs"] = kwargs
        return _StubClient()

    storage = MinioObjectStorage(
        bucket="probe-bucket",
        endpoint_url="http://minio:9000",
        access_key="ak",
        secret_key="sk",
        region="us-east-1",
    )
    with patch("boto3.client", side_effect=_stub_boto3_client):
        storage.readiness_probe(timeout_seconds=2)

    kwargs = captured["client_kwargs"]
    assert kwargs.get("endpoint_url") == "http://minio:9000"
    assert kwargs.get("aws_access_key_id") == "ak"
    assert kwargs.get("aws_secret_access_key") == "sk"
    cfg = kwargs.get("config")
    assert cfg is not None
    s3_cfg = getattr(cfg, "s3", None) or {}
    assert s3_cfg.get("addressing_style") == "path", (
        "MinIO readiness Config must keep `addressing_style=path`; "
        f"got {s3_cfg!r}."
    )
    assert cfg.connect_timeout == 2 and cfg.read_timeout == 2
    retries = getattr(cfg, "retries", None) or {}
    assert retries.get("max_attempts") == 1


def test_pg_check_uses_statement_timeout_in_source():
    """Source-bound mirror of the behavioural test above. Removing
    the `SET LOCAL statement_timeout` from `check_postgres` would
    silently re-introduce the unbounded probe — this lock catches
    that diff at PR-review time without exercising the runtime.

    Uses raw text (NOT `python_code_only`) because the SQL fragment
    is a string literal that the tokenizer would strip; the
    behavioural test above (`test_check_postgres_uses_statement_timeout`)
    is the runtime guard against the docstring-only false positive."""
    text = _HEALTH_PY.read_text(encoding="utf-8")
    assert "SET LOCAL statement_timeout" in text, (
        "check_postgres() must `SET LOCAL statement_timeout = Nms` "
        "on the probe transaction. Without this bound a stalled PG "
        "(locks, exhausted backends) would block /ready beyond the "
        "kernel TCP keepalive."
    )


def test_object_storage_check_uses_readiness_probe_in_source():
    """Source-bound mirror: `check_object_storage` MUST reference
    `readiness_probe`. Reverting to `list_objects` would re-introduce
    the unbounded probe path. The behavioural test above
    (`test_check_object_storage_uses_bounded_probe_method`) guards
    against a comment-only false positive."""
    text = _HEALTH_PY.read_text(encoding="utf-8")
    assert "readiness_probe" in text, (
        "check_object_storage() must call `storage.readiness_probe("
        "timeout_seconds=...)`. The runtime `list_objects` path uses "
        "boto3 default timeouts (60s+60s with 3 retries = ~360s) "
        "and is unsafe on the readiness path."
    )
