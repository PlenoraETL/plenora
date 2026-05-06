"""Cost-boundedness lock for `gather_readiness()` checks.

BE-GO-READY-OBSERVABILITY-ENGINE-025-READINESS-COST-BOUNDEDNESS-AUDIT.

Locks the architectural rules from the audit:

  - `/ready` is composed of bounded checks only (cheap/local,
    DB-bounded query, or network-bounded with explicit timeout
    or capability-disabled skip path);
  - heavy checks are opt-in-disabled by default OR have an
    explicit fail-OPEN skip path so dev/test/CLI environments
    do NOT trigger 503 unexpectedly;
  - new checks added to `gather_readiness()` MUST follow the
    same pattern (no direct `requests.get` / `urllib` /
    `http.client` without explicit timeout).

These tests are SOURCE-BOUND for the network-call lock + setting
defaults, and behavioural for the keyset count + skip paths.
The audit document
(`.agent-temp/BE_INSTALLATION_AUDIT.md` §19) contains the full
classification and rationale.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from tests2.shared._source_scan import python_code_only as _python_code_only


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_HEALTH_PY = (
    _REPO_ROOT / "backend" / "app" / "observability" / "health.py"
)


# =====================================================================
# 1. Default-disabled posture for opt-in checks
# =====================================================================
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


# =====================================================================
# 2. Capability-disabled / threshold-disabled skip paths produce 200
# =====================================================================


def test_object_storage_skip_path_returns_true():
    """STORAGE_POLICY=db / capability disabled MUST return
    `(True, "skipped: capability disabled")` and NOT trigger 503."""
    from app.observability import health as h

    with patch(
        "app.shared.cloud_backends.object_storage_enabled",
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
    from app.shared.cloud_backends import ObjectStorageDisabledError

    def _raise_disabled():
        raise ObjectStorageDisabledError("capability_disabled_race")

    with patch(
        "app.shared.cloud_backends.object_storage_enabled",
        return_value=True,
    ), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
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


# =====================================================================
# 3. Keyset count + ordering lock
# =====================================================================
#
# The 8-key shape is asserted by
# `tests2/observability/test_health_module_contract.py::
# test_gather_readiness_returns_200_when_all_8_ok`. We add a
# complementary lock that fails LOUDLY if a 9th check is added
# without going through the audit (sec. §19.4 of
# BE_INSTALLATION_AUDIT.md).


def test_gather_readiness_keyset_is_exactly_8():
    """Adding a 9th check MUST be paired with an audit update.
    This guard prevents silent expansion of the readiness surface.

    If you are adding a 9th check, ALSO update:
      - `.agent-temp/BE_INSTALLATION_AUDIT.md` §19 (cost classification);
      - `tests2/observability/test_health_module_contract.py`
        (rename the keyset test to reflect the new count);
      - this assertion."""
    from app.observability import health as h

    # Stub all current checks to OK so the test does not depend on
    # local state. Any new check added without updating this stub
    # list would fail the assertion below.
    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_redis", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", return_value=(True, "ok")), \
         patch.object(h, "check_migrations_aligned", return_value=(True, "ok")), \
         patch.object(h, "check_object_storage", return_value=(True, "ok")), \
         patch.object(h, "check_reconciliation_backlog", return_value=(True, "ok")), \
         patch.object(h, "check_beat_heartbeat", return_value=(True, "ok")), \
         patch.object(h, "check_bootstrap_complete", return_value=(True, "ok")):
        payload = h.gather_readiness()

    assert len(payload.body["checks"]) == 8, (
        f"Readiness check count regressed: got "
        f"{sorted(payload.body['checks'].keys())}. If you are adding a "
        "9th check, see the audit guidance in the docstring above."
    )


def test_gather_readiness_keyset_canonical_order():
    """The 8 keys are inserted in a stable order (Python 3.7+ dict
    preserves insertion order). The ordering matters for operators
    grepping `curl /ready | jq -r '.checks | keys[]'` — flipping
    e.g. `bootstrap_complete` BEFORE `beat_heartbeat` would make
    a stale dashboard alert fire on the wrong key."""
    from app.observability import health as h

    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_redis", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", return_value=(True, "ok")), \
         patch.object(h, "check_migrations_aligned", return_value=(True, "ok")), \
         patch.object(h, "check_object_storage", return_value=(True, "ok")), \
         patch.object(h, "check_reconciliation_backlog", return_value=(True, "ok")), \
         patch.object(h, "check_beat_heartbeat", return_value=(True, "ok")), \
         patch.object(h, "check_bootstrap_complete", return_value=(True, "ok")):
        payload = h.gather_readiness()

    expected_order = [
        "postgres",
        "redis",
        "pool_headroom",
        "migrations",
        "object_storage",
        "reconciliation_backlog",
        "beat_heartbeat",
        "bootstrap_complete",
    ]
    actual_order = list(payload.body["checks"].keys())
    assert actual_order == expected_order, (
        f"Readiness key order changed: expected {expected_order}, "
        f"got {actual_order}. Reorder the gather_readiness() body or "
        "update this assertion + the audit document explicitly."
    )


# =====================================================================
# 4. Source-bound: no direct network call without timeout/skip path
# =====================================================================
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
    text = _HEALTH_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)
    assert "socket_connect_timeout" in code_only, (
        "check_redis() must use `redis.from_url(..., "
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


# =====================================================================
# 5. Defense-in-depth: heavy checks declare fail-OPEN posture
# =====================================================================
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
    readiness (every other DB-backed check would lie)."""
    from app.observability import health as h

    def _raise(sql, params=()):
        raise RuntimeError("PG unreachable")

    with patch("app.db.pg_connection.fetchone", _raise):
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


# =====================================================================
# 6. OPS-001: liveness cheapness + Redis PING-level timeout
# =====================================================================
#
# BE-GO-READY-OPS-001-READINESS-COST-BOUNDEDNESS adds three locks:
#   (a) liveness must NEVER call any of the 6 readiness-only checks
#       (load-balancer-friendly latency budget);
#   (b) Redis PING must be bounded at the OPERATION level
#       (`socket_timeout`), not only at the TCP handshake level
#       (`socket_connect_timeout`);
#   (c) the Redis timeout is configurable via
#       `READINESS_REDIS_TIMEOUT_SECONDS` so operators can tune
#       per-deploy without source edits.


def test_gather_liveness_keyset_is_exactly_two():
    """Liveness keyset MUST be exactly `{postgres, redis}`. Adding
    any of the 6 readiness-only checks to liveness would expand the
    /health latency budget beyond the load-balancer-friendly target
    (PG SELECT 1 + Redis PING ~ low-ms each, bounded by
    READINESS_REDIS_TIMEOUT_SECONDS).

    A k8s liveness probe failure kills the pod; a readiness probe
    failure only removes it from the load-balancer pool. Liveness
    must therefore be the cheapest, most boring check possible."""
    from app.observability import health as h

    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_redis", return_value=(True, "ok")):
        payload = h.gather_liveness()

    keys = list(payload.body["checks"].keys())
    assert keys == ["postgres", "redis"], (
        f"gather_liveness keyset changed: expected ['postgres', "
        f"'redis'], got {keys}. Liveness must NOT include any of "
        "the 6 readiness-only checks (pool_headroom, migrations, "
        "object_storage, reconciliation_backlog, beat_heartbeat, "
        "bootstrap_complete). A liveness probe failure kills the "
        "pod; only the cheapest checks belong here."
    )


def test_gather_liveness_does_not_call_readiness_only_checks():
    """If liveness called any of the 6 readiness-only checks they
    would fire here (we patch them to raise). Patching the 2
    liveness checks to OK + the 6 readiness checks to RAISE proves
    liveness never touches them."""
    from app.observability import health as h

    def _explode():
        raise AssertionError(
            "Readiness-only check called from gather_liveness — "
            "this would expand the /health latency budget."
        )

    with patch.object(h, "check_postgres", return_value=(True, "ok")), \
         patch.object(h, "check_redis", return_value=(True, "ok")), \
         patch.object(h, "check_pool_headroom", side_effect=_explode), \
         patch.object(h, "check_migrations_aligned", side_effect=_explode), \
         patch.object(h, "check_object_storage", side_effect=_explode), \
         patch.object(h, "check_reconciliation_backlog", side_effect=_explode), \
         patch.object(h, "check_beat_heartbeat", side_effect=_explode), \
         patch.object(h, "check_bootstrap_complete", side_effect=_explode):
        payload = h.gather_liveness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ok"


def test_redis_check_uses_socket_operation_timeout():
    """Source-bound: `check_redis` must pass BOTH
    `socket_connect_timeout` (TCP handshake bound) AND
    `socket_timeout` (PING-operation bound). Without the
    operation timeout, a Redis that accepts the TCP handshake but
    stalls before responding to PING would block /health/ready
    until the kernel TCP keepalive fires (~minutes)."""
    text = _HEALTH_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)
    assert "socket_timeout" in code_only, (
        "check_redis() must pass `socket_timeout=` in addition to "
        "`socket_connect_timeout=`. Without it, a Redis that accepts "
        "the connection but stalls before responding to PING would "
        "block /health/ready beyond the connect timeout window."
    )


def test_redis_timeout_setting_default_is_two_seconds():
    """`READINESS_REDIS_TIMEOUT_SECONDS` default = 2. Bounds both
    the TCP handshake and the PING. Operators may tune per-deploy
    (k8s tight SLO -> 1; noisy network -> 5). Cap <= 0 falls back
    to the hardcoded 2 (intentionally NOT disable-able: /health
    without a bound would be a footgun)."""
    from app.config import Settings

    val = Settings.DEFAULTS["READINESS_REDIS_TIMEOUT_SECONDS"]
    assert val == 2, (
        f"READINESS_REDIS_TIMEOUT_SECONDS default must be 2 seconds. "
        f"Got {val!r}. Tightening below 2s risks false positives on "
        "warm-restart Redis pods; raising above 5s starts to drift "
        "outside the load-balancer-friendly latency budget for "
        "/health."
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
        "from app.shared.cloud_backends",
        "from app.shared.storage_reconciliation",
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
