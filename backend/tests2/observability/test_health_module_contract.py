"""Contract tests BE-GO-READY-BOOTSTRAP-ENGINE-009-HEALTH-EXTRACTION.

Lock the new boundary `app/observability/health.py`:

1. Module purity: no FastAPI / Starlette import.
2. Public API: 4 `check_*` + 2 `gather_*` + `HealthPayload`
   dataclass + `CheckOutcome` type alias.
3. `check_*` outcome shape: `(bool, str)`.
4. `check_postgres` / `check_redis` fail-CLOSED on exception.
5. `check_pool_headroom` / `check_migrations_aligned` fail-OPEN on
   exception (preserved fallback from main.py).
6. `gather_liveness`: 200/503 + status "ok"/"unhealthy" + 2 checks.
7. `gather_readiness`: 200/503 + status "ready"/"not_ready" + 4 checks.
8. `HealthPayload` is frozen.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.observability, pytest.mark.contract]


# ---------------------------------------------------------------------------
# 1. Module purity: leaf module, no FastAPI / Starlette
# ---------------------------------------------------------------------------


def test_module_does_not_import_fastapi_or_starlette():
    """Boundary deve restare puro: estrazione health da main.py NON
    deve trascinare FastAPI/Starlette dentro un modulo che potrebbe
    essere importato da CLI / worker / test isolati."""
    import app.observability.health as h

    src = Path(h.__file__).read_text(encoding="utf-8")
    forbidden = ("fastapi", "starlette")
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for f in forbidden:
            assert f not in line.lower(), (
                f"app/observability/health.py importa '{f}' a riga: {line!r}. "
                "Il modulo deve restare leaf."
            )


def test_module_exposes_public_api():
    from app.observability.health import (
        CheckOutcome,
        HealthPayload,
        check_postgres,
        check_redis,
        check_pool_headroom,
        check_migrations_aligned,
        gather_liveness,
        gather_readiness,
    )
    # CheckOutcome e' un type alias (non testabile come instance);
    # la sua presenza nel namespace e' il lock.
    assert callable(check_postgres)
    assert callable(check_redis)
    assert callable(check_pool_headroom)
    assert callable(check_migrations_aligned)
    assert callable(gather_liveness)
    assert callable(gather_readiness)
    assert HealthPayload is not None


# ---------------------------------------------------------------------------
# 2. HealthPayload dataclass — frozen
# ---------------------------------------------------------------------------


def test_health_payload_is_frozen():
    from app.observability.health import HealthPayload

    p = HealthPayload(status_code=200, body={"status": "ok", "checks": {}})
    with pytest.raises(Exception):
        p.status_code = 503  # type: ignore[misc]


def test_health_payload_fields():
    from app.observability.health import HealthPayload

    p = HealthPayload(status_code=503, body={"status": "unhealthy", "checks": {}})
    assert p.status_code == 503
    assert p.body == {"status": "unhealthy", "checks": {}}


# ---------------------------------------------------------------------------
# 3. check_postgres — fail-CLOSED on exception
# ---------------------------------------------------------------------------


def test_check_postgres_ok_on_select_1():
    with patch("app.db.pg_connection.fetchone", return_value={"v": 1}):
        from app.observability.health import check_postgres

        ok, detail = check_postgres()
    assert ok is True
    assert detail == "ok"


def test_check_postgres_failed_on_unexpected_row():
    with patch("app.db.pg_connection.fetchone", return_value=None):
        from app.observability.health import check_postgres

        ok, detail = check_postgres()
    assert ok is False
    assert "SELECT 1 ha restituito" in detail


def test_check_postgres_fail_closed_on_exception():
    def _raise(*a, **kw):
        raise RuntimeError("DB down")

    with patch("app.db.pg_connection.fetchone", _raise):
        from app.observability.health import check_postgres

        ok, detail = check_postgres()
    assert ok is False
    assert detail == "RuntimeError: DB down"


# ---------------------------------------------------------------------------
# 4. check_redis — fail-CLOSED on exception
# ---------------------------------------------------------------------------


def test_check_redis_ok_on_ping_true():
    import redis

    fake_client = type("F", (), {"ping": lambda self: True})()
    with patch.object(redis, "from_url", return_value=fake_client):
        from app.observability.health import check_redis

        ok, detail = check_redis()
    assert ok is True
    assert detail == "ok"


def test_check_redis_failed_on_ping_false():
    import redis

    fake_client = type("F", (), {"ping": lambda self: False})()
    with patch.object(redis, "from_url", return_value=fake_client):
        from app.observability.health import check_redis

        ok, detail = check_redis()
    assert ok is False
    assert "PING non ha risposto True" in detail


def test_check_redis_fail_closed_on_exception():
    import redis

    def _raise(*a, **kw):
        raise ConnectionError("boom")

    with patch.object(redis, "from_url", _raise):
        from app.observability.health import check_redis

        ok, detail = check_redis()
    assert ok is False
    assert detail == "ConnectionError: boom"


# ---------------------------------------------------------------------------
# 5. check_pool_headroom — fail-OPEN on exception (preserved fallback)
# ---------------------------------------------------------------------------


def test_check_pool_headroom_fail_open_on_introspection_error():
    """BE-GO-READY-BOOTSTRAP-ENGINE-010: il consumer e' ora il public
    helper `pg_connection.get_pool_headroom()`. Se il helper solleva
    (lazy-init pool fallito, etc.), `check_pool_headroom` deve
    catchare e ritornare fail-OPEN — preservando il behavior pre-batch
    da main.py."""
    def _raise():
        raise RuntimeError("pool not init")

    with patch("app.db.pg_connection.get_pool_headroom", _raise):
        from app.observability.health import check_pool_headroom

        ok, detail = check_pool_headroom()
    assert ok is True
    assert "introspection skipped" in detail


def test_check_pool_headroom_failed_when_pool_saturo():
    """Pool saturo (in_use >= max): outcome `(False, "pool saturo: …")`.
    Detail string preservato byte-per-byte da pre-batch-010."""
    from app.db.pg_connection import PoolHeadroom

    headroom = PoolHeadroom(max=3, in_use=3, free=0)
    with patch("app.db.pg_connection.get_pool_headroom", return_value=headroom):
        from app.observability.health import check_pool_headroom

        ok, detail = check_pool_headroom()
    assert ok is False
    assert "pool saturo" in detail
    assert "3/3 in uso" in detail
    assert "0 free" in detail


def test_check_pool_headroom_ok_with_free_slots():
    """Headroom disponibile: outcome `(True, "<used>/<max> in uso, <free> free")`."""
    from app.db.pg_connection import PoolHeadroom

    headroom = PoolHeadroom(max=12, in_use=1, free=2)
    with patch("app.db.pg_connection.get_pool_headroom", return_value=headroom):
        from app.observability.health import check_pool_headroom

        ok, detail = check_pool_headroom()
    assert ok is True
    assert "1/12 in uso" in detail
    assert "2 free" in detail


def test_check_pool_headroom_does_not_import_private_pool_symbols():
    """Source-bound: `health.py` NON deve piu' importare `_get_pool`
    o `_POOL_MAX`. La dipendenza dai privati e' stata incapsulata in
    `pg_connection.get_pool_headroom`.

    Lo scan e' limitato alle linee `import` / `from` per evitare
    falsi positivi sulle citazioni docstring (le docstring possono
    riferirsi ai nomi privati nei commenti storici).
    """
    import app.observability.health as h

    src = Path(h.__file__).read_text(encoding="utf-8")
    forbidden = ("_get_pool", "_POOL_MAX")
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for sym in forbidden:
            assert sym not in line, (
                f"app/observability/health.py importa ancora il simbolo "
                f"privato {sym!r} a riga: {line!r}. Usa "
                f"`pg_connection.get_pool_headroom()` come public helper."
            )
    # Public helper consumed.
    assert "get_pool_headroom" in src


# ---------------------------------------------------------------------------
# 6. check_migrations_aligned — fail-OPEN on exception
# ---------------------------------------------------------------------------


def test_check_migrations_aligned_ok_when_no_pending():
    with patch(
        "app.db.migrations_runner.list_pending_migrations",
        return_value=[],
    ):
        from app.observability.health import check_migrations_aligned

        ok, detail = check_migrations_aligned()
    assert ok is True
    assert detail == "aligned"


def test_check_migrations_aligned_failed_with_pending_list():
    with patch(
        "app.db.migrations_runner.list_pending_migrations",
        return_value=["2026-05-01_a.sql", "2026-05-02_b.sql"],
    ):
        from app.observability.health import check_migrations_aligned

        ok, detail = check_migrations_aligned()
    assert ok is False
    assert "2 pending" in detail
    assert "2026-05-01_a.sql" in detail


def test_check_migrations_aligned_fail_open_on_exception():
    def _raise():
        raise RuntimeError("introspection bug")

    with patch(
        "app.db.migrations_runner.list_pending_migrations", _raise
    ):
        from app.observability.health import check_migrations_aligned

        ok, detail = check_migrations_aligned()
    # Fail-OPEN preserved from main.py.
    assert ok is True
    assert "check skipped" in detail


# ---------------------------------------------------------------------------
# 7. gather_liveness — 200/503 + 2 checks
# ---------------------------------------------------------------------------


def test_gather_liveness_returns_200_when_pg_and_redis_ok():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
    ):
        payload = h.gather_liveness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ok"
    assert payload.body["checks"]["postgres"] == {"ok": True, "detail": "ok"}
    assert payload.body["checks"]["redis"] == {"ok": True, "detail": "ok"}


def test_gather_liveness_returns_503_when_pg_fails():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(False, "RuntimeError: x")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
    ):
        payload = h.gather_liveness()

    assert payload.status_code == 503
    assert payload.body["status"] == "unhealthy"
    assert payload.body["checks"]["postgres"]["ok"] is False
    assert payload.body["checks"]["redis"]["ok"] is True


def test_gather_liveness_returns_503_when_redis_fails():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(False, "ConnectionError: x")),
    ):
        payload = h.gather_liveness()

    assert payload.status_code == 503
    assert payload.body["status"] == "unhealthy"


def test_gather_liveness_only_2_checks_in_body():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
    ):
        payload = h.gather_liveness()

    # Liveness shape: solo postgres + redis, NO pool_headroom / migrations.
    assert set(payload.body["checks"].keys()) == {"postgres", "redis"}


# ---------------------------------------------------------------------------
# 8. gather_readiness — 200/503 + 5 checks (BE-GO-READY-BOOTSTRAP-ENGINE-011)
# ---------------------------------------------------------------------------


def test_gather_readiness_returns_200_when_all_6_ok():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "1/12 in uso, 11 free")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(h, "check_object_storage", return_value=(True, "ok")),
        patch.object(h, "check_reconciliation_backlog", return_value=(True, "pending=0 lost=0")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ready"
    assert set(payload.body["checks"].keys()) == {
        "postgres", "redis", "pool_headroom", "migrations",
        "object_storage", "reconciliation_backlog",
    }


def test_gather_readiness_returns_200_when_object_storage_skipped_capability_disabled():
    """BE-GO-READY-BOOTSTRAP-ENGINE-011: capability-off non e' un
    errore. /ready resta 200 se gli altri 4 check sono ok e
    `object_storage` ritorna `(True, "skipped: capability disabled")`."""
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "ok")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(
            h, "check_object_storage",
            return_value=(True, "skipped: capability disabled"),
        ),
        patch.object(h, "check_reconciliation_backlog", return_value=(True, "pending=0 lost=0")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ready"
    assert payload.body["checks"]["object_storage"]["ok"] is True
    assert "skipped" in payload.body["checks"]["object_storage"]["detail"]


def test_gather_readiness_returns_503_on_pool_saturo():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(False, "pool saturo")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(h, "check_object_storage", return_value=(True, "ok")),
        patch.object(h, "check_reconciliation_backlog", return_value=(True, "pending=0 lost=0")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["pool_headroom"]["ok"] is False


def test_gather_readiness_returns_503_on_pending_migrations():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "ok")),
        patch.object(h, "check_migrations_aligned", return_value=(False, "1 pending")),
        patch.object(h, "check_object_storage", return_value=(True, "ok")),
        patch.object(h, "check_reconciliation_backlog", return_value=(True, "pending=0 lost=0")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["migrations"]["ok"] is False


def test_gather_readiness_returns_503_on_object_storage_failure():
    """BE-GO-READY-BOOTSTRAP-ENGINE-011: storage capability ON ma il
    check fallisce (bucket assente / credenziali / network) -> 503."""
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "ok")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(
            h, "check_object_storage",
            return_value=(False, "EndpointConnectionError: timeout"),
        ),
        patch.object(h, "check_reconciliation_backlog", return_value=(True, "pending=0 lost=0")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["object_storage"]["ok"] is False
    assert "EndpointConnectionError" in payload.body["checks"]["object_storage"]["detail"]


def test_gather_readiness_returns_503_on_reconciliation_backlog_over_threshold():
    """BE-GO-READY-STORAGE-ENGINE-015: il backlog readiness check
    sopra threshold porta /ready a 503."""
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "ok")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
        patch.object(h, "check_object_storage", return_value=(True, "ok")),
        patch.object(
            h, "check_reconciliation_backlog",
            return_value=(False, "lost=42 over 10 (pending=0 lost=42)"),
        ),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["reconciliation_backlog"]["ok"] is False
    assert "lost=42" in payload.body["checks"]["reconciliation_backlog"]["detail"]


# ---------------------------------------------------------------------------
# 8.5 check_object_storage — capability-aware + boto3 not imported here
# (BE-GO-READY-BOOTSTRAP-ENGINE-011)
# ---------------------------------------------------------------------------


def test_check_object_storage_returns_skipped_when_capability_off():
    """`object_storage_enabled() == False` -> outcome
    `(True, "skipped: capability disabled")`. Modalita' on-prem
    legitima."""
    with (
        patch("app.shared.cloud_backends.object_storage_enabled", return_value=False),
    ):
        from app.observability.health import check_object_storage

        ok, detail = check_object_storage()
    assert ok is True
    assert detail == "skipped: capability disabled"


def test_check_object_storage_ok_when_list_objects_succeeds():
    """Capability ON + `list_objects(prefix=".plenora-readiness-probe/")` ritorna senza
    sollevare -> outcome `(True, "ok")`."""
    calls = []

    class _Storage:
        def list_objects(self, prefix):
            calls.append(prefix)
            return []

    with (
        patch("app.shared.cloud_backends.object_storage_enabled", return_value=True),
        patch(
            "app.shared.cloud_backends.get_runtime_object_storage",
            return_value=_Storage(),
        ),
    ):
        from app.observability.health import check_object_storage

        ok, detail = check_object_storage()
    assert ok is True
    assert detail == "ok"
    assert calls == [".plenora-readiness-probe/"]


def test_check_object_storage_failed_on_network_error():
    """Capability ON + `list_objects` solleva -> outcome
    `(False, "<ExcType>: <msg>")`."""
    class _RaisingStorage:
        def list_objects(self, prefix):
            raise ConnectionError("S3 endpoint unreachable")

    with (
        patch("app.shared.cloud_backends.object_storage_enabled", return_value=True),
        patch(
            "app.shared.cloud_backends.get_runtime_object_storage",
            return_value=_RaisingStorage(),
        ),
    ):
        from app.observability.health import check_object_storage

        ok, detail = check_object_storage()
    assert ok is False
    assert "ConnectionError" in detail
    assert "S3 endpoint unreachable" in detail


def test_check_object_storage_skipped_on_disabled_race():
    """`get_runtime_object_storage()` solleva
    `ObjectStorageDisabledError` (race tra capability check e
    factory) -> outcome `(True, "skipped: capability disabled (race)")`."""
    from app.shared.cloud_backends import ObjectStorageDisabledError

    def _raise():
        raise ObjectStorageDisabledError("capability went off")

    with (
        patch("app.shared.cloud_backends.object_storage_enabled", return_value=True),
        patch("app.shared.cloud_backends.get_runtime_object_storage", _raise),
    ):
        from app.observability.health import check_object_storage

        ok, detail = check_object_storage()
    assert ok is True
    assert "skipped" in detail
    assert "race" in detail


def test_check_object_storage_failed_on_factory_unexpected_error():
    """`get_runtime_object_storage()` solleva exception non-disabled
    (config malformata, credenziali mancanti) -> outcome
    `(False, "<ExcType>: ...")`."""
    def _raise():
        raise RuntimeError("AWS_ACCESS_KEY_ID not set")

    with (
        patch("app.shared.cloud_backends.object_storage_enabled", return_value=True),
        patch("app.shared.cloud_backends.get_runtime_object_storage", _raise),
    ):
        from app.observability.health import check_object_storage

        ok, detail = check_object_storage()
    assert ok is False
    assert "RuntimeError" in detail


def test_check_object_storage_does_not_import_boto3_directly():
    """Source-bound: `health.py` NON deve importare boto3 / botocore
    / S3 SDK. La SDK e' incapsulata dentro `cloud_backends`. Lo scan
    si limita alle linee `import` / `from`."""
    import app.observability.health as h

    src = Path(h.__file__).read_text(encoding="utf-8")
    forbidden = ("boto3", "botocore", "s3transfer")
    for line in src.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("import ") or stripped.startswith("from ")):
            continue
        for sym in forbidden:
            assert sym not in line, (
                f"app/observability/health.py importa SDK cloud {sym!r} "
                f"a riga: {line!r}. Usa cloud_backends boundary."
            )
    # Boundary consumato.
    assert "object_storage_enabled" in src
    assert "get_runtime_object_storage" in src


# ---------------------------------------------------------------------------
# 9. Source-bound: main.py delegates to gather_*, no inline _check_*
# ---------------------------------------------------------------------------


def test_main_py_delegates_health_routes_to_health_module():
    main_py = (
        Path(__file__).resolve().parent.parent.parent
        / "app" / "main.py"
    )
    src = main_py.read_text(encoding="utf-8")
    # Le route delegano agli helper estratti.
    assert "gather_liveness" in src
    assert "gather_readiness" in src
    assert "from app.observability.health import" in src
    # Le 4 funzioni `_check_*` originali NON devono piu' essere
    # definite in main.py (sono state estratte).
    assert "def _check_postgres" not in src
    assert "def _check_redis" not in src
    assert "def _check_pool_headroom" not in src
    assert "def _check_migrations_aligned" not in src


# ---------------------------------------------------------------------------
# 10. check_reconciliation_backlog — threshold-based readiness
# (BE-GO-READY-STORAGE-ENGINE-015)
# ---------------------------------------------------------------------------


def _mk_count_row(state: str, domain: str, count: int):
    """Costruisce un `ReconciliationStateCount` mock per i test."""
    from app.shared.storage_reconciliation import ReconciliationStateCount

    return ReconciliationStateCount(state=state, domain=domain, count=count)


def test_check_reconciliation_backlog_ok_when_empty():
    """Tabella vuota -> outcome (True, "pending=0 lost=0")."""
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=[],
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is True
    assert detail == "pending=0 lost=0"


def test_check_reconciliation_backlog_ok_when_below_thresholds():
    """Counts non zero ma sotto i threshold -> ok con detail tipato."""
    counts = [
        _mk_count_row("pending", "file_source", 5),
        _mk_count_row("pending", "result_blob", 10),
        _mk_count_row("lost", "file_source", 2),
    ]
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=counts,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is True
    assert detail == "pending=15 lost=2"


def test_check_reconciliation_backlog_failed_above_pending_threshold(monkeypatch):
    """`pending_total > STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD`
    -> outcome (False, ...)."""
    from app.config import settings as _settings
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD",
        100,
    )
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD",
        10,
    )
    counts = [
        _mk_count_row("pending", "file_source", 200),  # over 100
        _mk_count_row("lost", "file_source", 1),
    ]
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=counts,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is False
    assert "pending=200 over 100" in detail
    assert "pending=200 lost=1" in detail


def test_check_reconciliation_backlog_failed_above_lost_threshold(monkeypatch):
    """`lost_total > STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD`
    -> outcome (False, ...)."""
    from app.config import settings as _settings
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD",
        1000,
    )
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD",
        5,
    )
    counts = [
        _mk_count_row("pending", "file_source", 3),
        _mk_count_row("lost", "file_source", 15),  # over 5
    ]
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=counts,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is False
    assert "lost=15 over 5" in detail


def test_check_reconciliation_backlog_failed_on_both_thresholds(monkeypatch):
    """Entrambi sopra threshold -> detail menziona entrambi separati da ';'."""
    from app.config import settings as _settings
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD",
        50,
    )
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD",
        5,
    )
    counts = [
        _mk_count_row("pending", "file_source", 100),
        _mk_count_row("lost", "result_blob", 20),
    ]
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=counts,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is False
    assert "pending=100 over 50" in detail
    assert "lost=20 over 5" in detail
    assert ";" in detail


def test_check_reconciliation_backlog_skipped_when_pending_threshold_zero(monkeypatch):
    """Cap <= 0 disabilita il branch pending. Lost cap continua a
    essere applicato; se anche lost <= 0 il check e' sempre ok."""
    from app.config import settings as _settings
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD",
        0,
    )
    monkeypatch.setitem(
        _settings.DEFAULTS,
        "STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD",
        10,
    )
    # pending astronomico ma threshold disabilitato -> ok.
    counts = [
        _mk_count_row("pending", "file_source", 1_000_000),
        _mk_count_row("lost", "file_source", 1),
    ]
    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        return_value=counts,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is True
    assert "pending=1000000" in detail
    assert "lost=1" in detail


def test_check_reconciliation_backlog_fail_open_on_count_error():
    """`count_active_by_state_domain` returns [] su DB error
    (best-effort); il backlog check segue lo stesso fail-OPEN del
    pattern `check_pool_headroom`/`check_migrations_aligned`: se la
    lettura non e' possibile (lista vuota o exception), non
    blocchiamo /ready."""
    def _raise():
        raise RuntimeError("DB unreachable")

    with patch(
        "app.shared.storage_reconciliation.count_active_by_state_domain",
        _raise,
    ):
        from app.observability.health import check_reconciliation_backlog

        ok, detail = check_reconciliation_backlog()
    assert ok is True
    assert "skipped" in detail
    assert "RuntimeError" in detail


def test_check_reconciliation_backlog_does_not_import_db_directly():
    """Source-bound: `health.py` non deve importare `pg_connection`
    o moduli DB direttamente per il backlog check; consume del
    boundary `count_active_by_state_domain` esistente."""
    import app.observability.health as h

    src = Path(h.__file__).read_text(encoding="utf-8")
    # Public boundary consumed.
    assert "count_active_by_state_domain" in src
    # `check_reconciliation_backlog` chiamata in `gather_readiness`.
    assert "check_reconciliation_backlog" in src


def test_settings_defaults_expose_backlog_thresholds():
    """Settings.DEFAULTS deve esporre i 2 threshold con int positivi
    (default-on) — ops puo' settare <= 0 per disabilitare un branch."""
    from app.config import Settings

    pending_thr = Settings.DEFAULTS.get("STORAGE_RECONCILE_BACKLOG_PENDING_503_THRESHOLD")
    lost_thr = Settings.DEFAULTS.get("STORAGE_RECONCILE_BACKLOG_LOST_503_THRESHOLD")
    assert isinstance(pending_thr, int) and pending_thr > 0
    assert isinstance(lost_thr, int) and lost_thr > 0
    # Lost threshold deve essere piu' rigoroso (ogni lost = orphan
    # blob, dovrebbe essere raro).
    assert lost_thr <= pending_thr
