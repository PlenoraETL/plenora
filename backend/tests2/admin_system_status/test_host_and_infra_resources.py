"""Contract test — metriche host + infra (psutil, DB pool, Redis, schema size).

Scope: degraded paths (psutil missing, DB error, Redis down) devono sempre
ritornare un dict valido con `status`. Mai sollevare al chiamante.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


# =============================================================================
# get_host_resources — psutil
# =============================================================================


def test_host_resources_when_psutil_missing(monkeypatch):
    """Se psutil non importabile → status=unknown, niente crash."""
    from app.core.admin_system_status import service

    # Blocca l'import di psutil iniettando una poison entry
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("forzato dal test")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        out = service.get_host_resources()

    assert out["status"] == "unknown"
    assert "psutil" in out["message"]
    assert out["disk"] is None
    assert out["memory"] is None
    assert out["cpu"] is None


def test_host_resources_happy_path(monkeypatch):
    """Con psutil mockato: shape stabile, status OK se tutti < soglie."""
    from app.core.admin_system_status import service

    fake_psutil = MagicMock()
    fake_psutil.disk_usage.return_value = MagicMock(
        total=100 * 1024 ** 3,  # 100 GB
        used=50 * 1024 ** 3,
        free=50 * 1024 ** 3,
        percent=50.0,
    )
    fake_psutil.virtual_memory.return_value = MagicMock(
        total=16 * 1024 ** 3,
        used=8 * 1024 ** 3,
        available=8 * 1024 ** 3,
        percent=50.0,
    )
    fake_psutil.getloadavg.return_value = (1.0, 1.5, 2.0)
    fake_psutil.cpu_count.return_value = 4
    fake_psutil.cpu_percent.return_value = 25.0

    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()

    assert out["status"] == "ok"
    assert out["disk"]["percent"] == 50.0
    assert out["memory"]["percent"] == 50.0
    assert out["cpu"]["count"] == 4
    assert out["cpu"]["load_1m"] == 1.0
    assert "disco" in out["message"]


def test_host_resources_disk_over_error_threshold(monkeypatch):
    """Disk > 92% → status=error."""
    from app.core.admin_system_status import service

    fake_psutil = MagicMock()
    fake_psutil.disk_usage.return_value = MagicMock(
        total=100 * 1024 ** 3, used=95 * 1024 ** 3, free=5 * 1024 ** 3, percent=95.0,
    )
    fake_psutil.virtual_memory.return_value = MagicMock(
        total=16 * 1024 ** 3, used=4 * 1024 ** 3, available=12 * 1024 ** 3, percent=25.0,
    )
    fake_psutil.getloadavg.return_value = (0.5, 0.5, 0.5)
    fake_psutil.cpu_count.return_value = 4
    fake_psutil.cpu_percent.return_value = 10.0
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()
    assert out["status"] == "error"
    assert out["disk"]["percent"] == 95.0


def test_host_resources_memory_over_warn_threshold(monkeypatch):
    """Memory 85% → warn (no error)."""
    from app.core.admin_system_status import service

    fake_psutil = MagicMock()
    fake_psutil.disk_usage.return_value = MagicMock(
        total=100 * 1024 ** 3, used=30 * 1024 ** 3, free=70 * 1024 ** 3, percent=30.0,
    )
    fake_psutil.virtual_memory.return_value = MagicMock(
        total=16 * 1024 ** 3, used=14 * 1024 ** 3, available=2 * 1024 ** 3, percent=87.0,
    )
    fake_psutil.getloadavg.return_value = (0.5, 0.5, 0.5)
    fake_psutil.cpu_count.return_value = 4
    fake_psutil.cpu_percent.return_value = 10.0
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()
    assert out["status"] == "warn"
    assert out["memory"]["percent"] == 87.0


def test_host_resources_no_loadavg_on_windows(monkeypatch):
    """psutil.getloadavg() solleva AttributeError su Windows → load_*m None,
    non crash."""
    from app.core.admin_system_status import service

    fake_psutil = MagicMock()
    fake_psutil.disk_usage.return_value = MagicMock(
        total=100 * 1024 ** 3, used=30 * 1024 ** 3, free=70 * 1024 ** 3, percent=30.0,
    )
    fake_psutil.virtual_memory.return_value = MagicMock(
        total=16 * 1024 ** 3, used=4 * 1024 ** 3, available=12 * 1024 ** 3, percent=25.0,
    )
    fake_psutil.getloadavg.side_effect = AttributeError("Windows non ha load avg")
    fake_psutil.cpu_count.return_value = 4
    fake_psutil.cpu_percent.return_value = 10.0
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()
    assert out["status"] == "ok"
    assert out["cpu"]["load_1m"] is None
    assert out["cpu"]["load_5m"] is None
    assert out["cpu"]["load_15m"] is None


# =============================================================================
# get_db_pool_status
# =============================================================================


def test_db_pool_when_db_fails_returns_unknown(monkeypatch):
    from app.core.admin_system_status import service

    with patch("app.db.pg_connection.fetchone", side_effect=ConnectionError("down")):
        out = service.get_db_pool_status()

    assert out["status"] == "unknown"
    assert out["message"] == "DB non accessibile"


def test_db_pool_under_warn_threshold_is_ok(monkeypatch):
    from app.core.admin_system_status import service

    row = {
        "max_conn": 100, "total": 20, "active": 5, "idle": 15,
        "idle_in_tx": 0, "waiting": 0,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_db_pool_status()

    assert out["status"] == "ok"
    assert out["total"] == 20
    assert out["percent"] == 20.0


def test_db_pool_warn_threshold(monkeypatch):
    from app.core.admin_system_status import service

    row = {
        "max_conn": 100, "total": 82, "active": 50, "idle": 32,
        "idle_in_tx": 0, "waiting": 0,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_db_pool_status()

    assert out["status"] == "warn"


def test_db_pool_error_threshold(monkeypatch):
    from app.core.admin_system_status import service

    row = {
        "max_conn": 100, "total": 97, "active": 80, "idle": 17,
        "idle_in_tx": 0, "waiting": 2,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_db_pool_status()

    assert out["status"] == "error"


def test_db_pool_many_idle_in_transaction_triggers_warn(monkeypatch):
    """idle_in_transaction > 5 → warn anche se pct basso."""
    from app.core.admin_system_status import service

    row = {
        "max_conn": 100, "total": 15, "active": 2, "idle": 3,
        "idle_in_tx": 10, "waiting": 0,
    }
    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_db_pool_status()

    assert out["status"] == "warn"


# =============================================================================
# get_redis_status
# =============================================================================


def test_redis_unreachable_returns_error(monkeypatch):
    """redis.Redis.from_url raise → status=error con messaggio leggibile."""
    from app.core.admin_system_status import service

    fake_redis_mod = MagicMock()
    fake_redis_mod.Redis.from_url.side_effect = ConnectionError("broker down")
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "error"
    assert "Redis non raggiungibile" in out["message"]


def test_redis_info_happy_path(monkeypatch):
    from app.core.admin_system_status import service

    fake_client = MagicMock()
    fake_client.info.return_value = {
        "used_memory": 50 * 1024 * 1024,
        "used_memory_human": "50M",
        "maxmemory": 200 * 1024 * 1024,
        "connected_clients": 12,
        "uptime_in_seconds": 86400 * 3,
    }

    fake_redis_mod = MagicMock()
    fake_redis_mod.Redis.from_url.return_value = fake_client
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "ok"
    assert out["percent"] == 25.0  # 50/200
    assert out["connected_clients"] == 12
    assert "uptime 3d" in out["message"]


def test_redis_over_warn_threshold(monkeypatch):
    from app.core.admin_system_status import service

    fake_client = MagicMock()
    fake_client.info.return_value = {
        "used_memory": 80 * 1024 * 1024,
        "used_memory_human": "80M",
        "maxmemory": 100 * 1024 * 1024,
        "connected_clients": 5,
        "uptime_in_seconds": 3600,
    }
    fake_redis_mod = MagicMock()
    fake_redis_mod.Redis.from_url.return_value = fake_client
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "warn"  # 80% >= 75%


def test_redis_without_maxmemory_has_no_percent(monkeypatch):
    """maxmemory=0 (unlimited): pct=None, status resta ok."""
    from app.core.admin_system_status import service

    fake_client = MagicMock()
    fake_client.info.return_value = {
        "used_memory": 500 * 1024 * 1024,
        "used_memory_human": "500M",
        "maxmemory": 0,
        "connected_clients": 3,
        "uptime_in_seconds": 600,
    }
    fake_redis_mod = MagicMock()
    fake_redis_mod.Redis.from_url.return_value = fake_client
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "ok"
    assert out["percent"] is None


# =============================================================================
# get_db_schema_size
# =============================================================================


def test_db_schema_size_when_db_fails_returns_unknown(monkeypatch):
    from app.core.admin_system_status import service

    with patch("app.db.pg_connection.fetchone", side_effect=ConnectionError("db down")):
        out = service.get_db_schema_size()

    assert out["status"] == "unknown"


def test_db_schema_size_returns_tables_sorted(monkeypatch):
    from app.core.admin_system_status import service

    size_row = {"size_pretty": "150 MB", "size_bytes": 150 * 1024 * 1024}
    table_rows = [
        {"table": "file_sources", "rows": 2, "size_pretty": "100 MB",
         "size_bytes": 100 * 1024 * 1024},
        {"table": "results", "rows": 50, "size_pretty": "40 MB",
         "size_bytes": 40 * 1024 * 1024},
        {"table": "schedule_logs", "rows": 1, "size_pretty": "10 MB",
         "size_bytes": 10 * 1024 * 1024},
    ]

    with patch("app.db.pg_connection.fetchone", return_value=size_row), \
         patch("app.db.pg_connection.fetchall", return_value=table_rows):
        out = service.get_db_schema_size()

    assert out["status"] == "ok"  # 150MB < 1GB warn threshold
    assert out["size_pretty"] == "150 MB"
    assert len(out["tables"]) == 3
    assert out["tables"][0]["name"] == "file_sources"  # piu' grande


def test_db_schema_size_over_warn_threshold(monkeypatch):
    """Schema > 1 GB (warn default)."""
    from app.core.admin_system_status import service

    size_row = {"size_pretty": "1.5 GB", "size_bytes": int(1.5 * 1024 ** 3)}
    with patch("app.db.pg_connection.fetchone", return_value=size_row), \
         patch("app.db.pg_connection.fetchall", return_value=[]):
        out = service.get_db_schema_size()

    assert out["status"] == "warn"


def test_db_schema_size_env_override(monkeypatch):
    """DB_SCHEMA_WARN_GB via env var shifta la soglia warn."""
    from app.core.admin_system_status import service

    monkeypatch.setenv("DB_SCHEMA_WARN_GB", "10")
    monkeypatch.setenv("DB_SCHEMA_ERROR_GB", "50")

    size_row = {"size_pretty": "2 GB", "size_bytes": 2 * 1024 ** 3}
    with patch("app.db.pg_connection.fetchone", return_value=size_row), \
         patch("app.db.pg_connection.fetchall", return_value=[]):
        out = service.get_db_schema_size()

    assert out["status"] == "ok"  # 2GB < 10GB warn (override)


# =============================================================================
# Aggregator include i nuovi blocchi
# =============================================================================


def test_aggregator_includes_new_blocks(monkeypatch):
    """get_system_status() ora ritorna anche host_resources, db_pool,
    redis, db_schema, jwt_blacklist."""
    from app.core.admin_system_status import service

    # Mock tutte le fonti esterne per non toccare DB/S3/redis reali
    monkeypatch.setattr(service, "get_backup_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_verify_restore_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_schedules_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_workers_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_host_resources", lambda: {"status": "ok", "disk": {}, "memory": {}, "cpu": {}})
    monkeypatch.setattr(service, "get_db_pool_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_redis_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_db_schema_size", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_blacklist_status", lambda: {"status": "ok"})
    monkeypatch.setattr(service, "get_tickets_status", lambda: {"status": "ok"})

    out = service.get_system_status()
    # Keys attese (contratto API)
    for k in ("backup", "verify_restore", "schedules", "workers",
              "host_resources", "db_pool", "redis", "db_schema",
              "jwt_blacklist", "tickets", "generated_at"):
        assert k in out, f"Mancante: {k}"


# =============================================================================
# get_blacklist_status — wrapper graceful su token_blacklist.get_blacklist_status
# =============================================================================


def test_blacklist_status_ok_via_wrapper(monkeypatch):
    """Happy path: il wrapper delega a token_blacklist.get_blacklist_status
    e ritorna il suo dict integro."""
    from app.core.admin_system_status import service
    import app.shared.token_blacklist as tb

    expected = {
        "status": "ok",
        "message": "Blacklist operativa",
        "counters": {"write_ok": 5, "write_fail": 0},
        "last_ok_epoch": 1700000000.0,
        "minutes_since_last_ok": 0.1,
    }
    monkeypatch.setattr(tb, "get_blacklist_status", lambda stale_threshold_seconds: expected)

    out = service.get_blacklist_status()
    assert out == expected


def test_blacklist_status_unknown_when_module_missing(monkeypatch):
    """Se token_blacklist.get_blacklist_status non importabile → unknown."""
    from app.core.admin_system_status import service

    # Iniettiamo un modulo finto senza get_blacklist_status
    import sys
    import types

    fake_mod = types.ModuleType("app.shared.token_blacklist")
    # NON settiamo `get_blacklist_status` su questo modulo → ImportError
    monkeypatch.setitem(sys.modules, "app.shared.token_blacklist", fake_mod)

    out = service.get_blacklist_status()
    assert out["status"] == "unknown"
    assert "non importabile" in out["message"].lower()


def test_blacklist_status_env_threshold_passed(monkeypatch):
    """L'env var JWT_BLACKLIST_STALE_THRESHOLD_SEC viene passato al probe."""
    from app.core.admin_system_status import service

    captured = {}

    def fake_probe(stale_threshold_seconds):
        captured["threshold"] = stale_threshold_seconds
        return {"status": "ok", "message": "fake"}

    import app.shared.token_blacklist as tb
    monkeypatch.setattr(tb, "get_blacklist_status", fake_probe)
    monkeypatch.setenv("JWT_BLACKLIST_STALE_THRESHOLD_SEC", "600")

    service.get_blacklist_status()
    assert captured["threshold"] == 600


def test_blacklist_status_env_threshold_invalid_fallback(monkeypatch):
    """Env non-int → fallback 1800."""
    from app.core.admin_system_status import service

    captured = {}

    def fake_probe(stale_threshold_seconds):
        captured["threshold"] = stale_threshold_seconds
        return {"status": "ok", "message": "fake"}

    import app.shared.token_blacklist as tb
    monkeypatch.setattr(tb, "get_blacklist_status", fake_probe)
    monkeypatch.setenv("JWT_BLACKLIST_STALE_THRESHOLD_SEC", "garbage")

    service.get_blacklist_status()
    assert captured["threshold"] == 1800


# =============================================================================
# get_tickets_status — graceful + soglie severity
# =============================================================================


def test_tickets_status_ok_when_no_critical(monkeypatch):
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    monkeypatch.setattr(tdb, "count_tickets", lambda **kw: 3)
    monkeypatch.setattr(tdb, "counts_by_severity",
                        lambda **kw: {"low": 1, "medium": 2, "high": 0, "critical": 0})

    out = service.get_tickets_status()
    assert out["status"] == "ok"
    assert out["total_active"] == 3
    assert out["by_severity"]["critical"] == 0


def test_tickets_status_ok_when_zero(monkeypatch):
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    monkeypatch.setattr(tdb, "count_tickets", lambda **kw: 0)
    monkeypatch.setattr(tdb, "counts_by_severity",
                        lambda **kw: {"low": 0, "medium": 0, "high": 0, "critical": 0})

    out = service.get_tickets_status()
    assert out["status"] == "ok"
    assert "nessun ticket" in out["message"].lower()


def test_tickets_status_warn_on_some_critical(monkeypatch):
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    monkeypatch.setattr(tdb, "count_tickets", lambda **kw: 5)
    monkeypatch.setattr(tdb, "counts_by_severity",
                        lambda **kw: {"low": 0, "medium": 2, "high": 2, "critical": 1})

    out = service.get_tickets_status()
    assert out["status"] == "warn"


def test_tickets_status_warn_on_many_high(monkeypatch):
    """high > 5 senza critical → warn (non ok)."""
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    monkeypatch.setattr(tdb, "count_tickets", lambda **kw: 8)
    monkeypatch.setattr(tdb, "counts_by_severity",
                        lambda **kw: {"low": 0, "medium": 0, "high": 8, "critical": 0})

    out = service.get_tickets_status()
    assert out["status"] == "warn"


def test_tickets_status_error_on_many_critical(monkeypatch):
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    monkeypatch.setattr(tdb, "count_tickets", lambda **kw: 10)
    monkeypatch.setattr(tdb, "counts_by_severity",
                        lambda **kw: {"low": 0, "medium": 1, "high": 2, "critical": 3})

    out = service.get_tickets_status()
    assert out["status"] == "error"
    assert "3 ticket critici" in out["message"]


def test_tickets_status_unknown_on_db_error(monkeypatch):
    from app.core.admin_system_status import service
    import app.db.tickets_db_service as tdb

    def boom(**kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(tdb, "count_tickets", boom)

    out = service.get_tickets_status()
    assert out["status"] == "unknown"
