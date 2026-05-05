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
# 8. gather_readiness — 200/503 + 4 checks
# ---------------------------------------------------------------------------


def test_gather_readiness_returns_200_when_all_4_ok():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(True, "1/12 in uso, 11 free")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 200
    assert payload.body["status"] == "ready"
    assert set(payload.body["checks"].keys()) == {
        "postgres", "redis", "pool_headroom", "migrations",
    }


def test_gather_readiness_returns_503_on_pool_saturo():
    import app.observability.health as h

    with (
        patch.object(h, "check_postgres", return_value=(True, "ok")),
        patch.object(h, "check_redis", return_value=(True, "ok")),
        patch.object(h, "check_pool_headroom", return_value=(False, "pool saturo")),
        patch.object(h, "check_migrations_aligned", return_value=(True, "aligned")),
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
    ):
        payload = h.gather_readiness()

    assert payload.status_code == 503
    assert payload.body["status"] == "not_ready"
    assert payload.body["checks"]["migrations"]["ok"] is False


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
