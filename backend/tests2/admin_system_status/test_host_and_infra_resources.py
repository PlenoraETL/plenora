"""
Contract test — metriche host + infra (psutil, DB pool, Redis, schema size).

Scope: degraded paths (psutil missing, DB error, Redis down) devono sempre
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


GB = 1024 ** 3


def _usage(total_gb, used_gb, free_gb, percent):
    return MagicMock(
        total=total_gb * GB,
        used=used_gb * GB,
        free=free_gb * GB,
        available=free_gb * GB,
        percent=percent,
    )


def _fake_psutil(
    *,
    disk_percent=30.0,
    memory_percent=25.0,
    loadavg=(0.5, 0.5, 0.5),
    cpu_count=4,
    cpu_percent=10.0,
):
    fake_psutil = MagicMock()
    fake_psutil.disk_usage.return_value = _usage(
        total_gb=100,
        used_gb=disk_percent,
        free_gb=100 - disk_percent,
        percent=disk_percent,
    )
    fake_psutil.virtual_memory.return_value = _usage(
        total_gb=16,
        used_gb=round(16 * memory_percent / 100, 2),
        free_gb=round(16 * (100 - memory_percent) / 100, 2),
        percent=memory_percent,
    )
    if isinstance(loadavg, BaseException):
        fake_psutil.getloadavg.side_effect = loadavg
    else:
        fake_psutil.getloadavg.return_value = loadavg
    fake_psutil.cpu_count.return_value = cpu_count
    fake_psutil.cpu_percent.return_value = cpu_percent
    return fake_psutil


def _db_pool_row(
    *,
    total,
    active=5,
    idle=15,
    idle_in_tx=0,
    waiting=0,
    max_conn=100,
):
    return {
        "max_conn": max_conn,
        "total": total,
        "active": active,
        "idle": idle,
        "idle_in_tx": idle_in_tx,
        "waiting": waiting,
    }


def _fake_redis_info(
    *,
    used_mb,
    max_mb,
    clients=5,
    uptime_seconds=3600,
):
    fake_client = MagicMock()
    fake_client.info.return_value = {
        "used_memory": used_mb * 1024 * 1024,
        "used_memory_human": f"{used_mb}M",
        "maxmemory": max_mb * 1024 * 1024,
        "connected_clients": clients,
        "uptime_in_seconds": uptime_seconds,
    }
    fake_redis_mod = MagicMock()
    fake_redis_mod.Redis.from_url.return_value = fake_client
    return fake_redis_mod


def _ticket_counts(*, low=0, medium=0, high=0, critical=0):
    return {
        "low": low,
        "medium": medium,
        "high": high,
        "critical": critical,
    }


# get_host_resources — psutil


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

    fake_psutil = _fake_psutil(
        disk_percent=50.0,
        memory_percent=50.0,
        loadavg=(1.0, 1.5, 2.0),
        cpu_percent=25.0,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()

    assert out["status"] == "ok"
    assert out["disk"]["percent"] == 50.0
    assert out["memory"]["percent"] == 50.0
    assert out["cpu"]["count"] == 4
    assert out["cpu"]["load_1m"] == 1.0
    assert "disco" in out["message"]


def test_host_resources_uses_existing_parent_for_missing_data_dir(monkeypatch, tmp_path):
    """DATA_DIR non ancora creato: misura il parent esistente, non crasha."""
    from app.core.admin_system_status import service
    import app.config as config_mod

    missing_data_dir = tmp_path / "missing" / "data"
    fake_settings = SimpleNamespace(DATA_DIR=missing_data_dir)

    fake_psutil = _fake_psutil(
        disk_percent=20.0,
        memory_percent=25.0,
        loadavg=AttributeError("Windows non ha load avg"),
    )

    monkeypatch.setattr(config_mod, "settings", fake_settings)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()

    fake_psutil.disk_usage.assert_called_once_with(str(tmp_path))
    assert out["status"] == "ok"
    assert out["disk"]["path"] == str(missing_data_dir)
    assert out["disk"]["usage_path"] == str(tmp_path)


def test_host_resources_falls_back_to_shutil_when_psutil_disk_fails(monkeypatch, tmp_path):
    """R-2 (2026-04-27, fix funzionale): su Windows locale `psutil.disk_usage`
    solleva `SystemError("argument 1 (impossible<bad format char>)")`.
    Previous behavior: warning + disk=None per ogni call.
    Current contract: fallback a `shutil.disk_usage` (stdlib, GetDiskFreeSpaceExW
    diretto). Se shutil ha successo, disk_info popolato e nessun warning
    di fallimento (solo logger.info diagnostico sul fallback)."""
    from app.core.admin_system_status import service
    import app.config as config_mod

    data_dir = tmp_path / "data"
    fake_settings = SimpleNamespace(DATA_DIR=data_dir)

    # psutil.disk_usage scoppia come in produzione locale
    boom = SystemError("argument 1 (impossible<bad format char>)")

    fake_psutil = _fake_psutil(
        memory_percent=25.0,
        loadavg=AttributeError("no load avg"),
    )
    fake_psutil.disk_usage.side_effect = boom

    # shutil.disk_usage ritorna namedtuple-like con total/used/free
    shutil_result = SimpleNamespace(
        total=200 * GB,
        used=50 * GB,
        free=150 * GB,
    )
    import shutil as _shutil_mod

    monkeypatch.setattr(config_mod, "settings", fake_settings)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(_shutil_mod, "disk_usage", lambda p: shutil_result)

    spy_warning = MagicMock()
    spy_info = MagicMock()
    monkeypatch.setattr(service.logger, "warning", spy_warning)
    monkeypatch.setattr(service.logger, "info", spy_info)

    out = service.get_host_resources()

    # disk_info popolato dal fallback shutil
    assert out["disk"] is not None, (
        "Atteso disk_info popolato dal fallback shutil, ricevuto None"
    )
    assert out["disk"]["path"] == str(data_dir)
    assert out["disk"]["total_gb"] == 200.0
    assert out["disk"]["used_gb"] == 50.0
    assert out["disk"]["free_gb"] == 150.0
    # percent calcolato manualmente: 50/200 = 25%
    assert out["disk"]["percent"] == 25.0

    # Memory + CPU normali
    assert out["memory"] is not None
    assert out["cpu"] is not None
    # Status ok (25% < 80% warn threshold)
    assert out["status"] == "ok"

    # NESSUN warning di fallimento finale (il fallback ha successo)
    assert spy_warning.call_count == 0, (
        f"Nessun warning atteso quando shutil ha successo, trovati: "
        f"{spy_warning.call_args_list}"
    )

    # Logger.info traccia il fallback (utile per ops in produzione,
    # informa che psutil ha fallito senza spammare warning)
    assert spy_info.call_count >= 1
    info_msgs = [c.args[0] for c in spy_info.call_args_list]
    fallback_msg = next(
        (m for m in info_msgs if "fallback shutil" in m), None
    )
    assert fallback_msg is not None, (
        f"Atteso info di fallback, trovati: {info_msgs}"
    )
    assert "SystemError" in fallback_msg
    assert "argument 1" in fallback_msg


def test_host_resources_logs_enriched_diagnostics_when_both_psutil_and_shutil_fail(
    monkeypatch, tmp_path
):
    """R-2 case negativo: se ANCHE shutil.disk_usage fallisce (es. drive
    smontato, permessi rotti), graceful con warning enriched diagnostico
    che include ENTRAMBI i tipi di eccezione (psutil + shutil)."""
    import struct as _struct

    from app.core.admin_system_status import service
    import app.config as config_mod

    data_dir = tmp_path / "data"
    fake_settings = SimpleNamespace(DATA_DIR=data_dir)

    psutil_boom = _struct.error("argument 1 (impossible<bad format char>)")
    shutil_boom = OSError("disk gone")

    fake_psutil = _fake_psutil(
        memory_percent=25.0,
        loadavg=AttributeError("no load avg"),
    )
    fake_psutil.disk_usage.side_effect = psutil_boom

    import shutil as _shutil_mod

    def _shutil_boom(p):
        raise shutil_boom

    monkeypatch.setattr(config_mod, "settings", fake_settings)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)
    monkeypatch.setattr(_shutil_mod, "disk_usage", _shutil_boom)

    spy_warning = MagicMock()
    monkeypatch.setattr(service.logger, "warning", spy_warning)

    out = service.get_host_resources()

    # Graceful: response prosegue, disk=None
    assert out["disk"] is None
    assert out["memory"] is not None
    assert out["cpu"] is not None

    # 1 warning enriched (entrambi gli errori dichiarati)
    assert spy_warning.call_count == 1
    msg = spy_warning.call_args.args[0]

    # Diagnostica enriched
    assert "host_resources disk error" in msg
    # shutil error e' quello "finale" -> tipo qualified
    assert "OSError" in msg
    assert "disk gone" in msg
    # data_dir / usage_path / type / cwd
    assert "data_dir=" in msg
    assert "usage_path=" in msg
    assert "usage_path_type=" in msg
    assert "cwd=" in msg
    # psutil_error dichiarato esplicito (cosi' sappiamo che il path
    # primario ha fallito con un'eccezione DIVERSA da quella shutil)
    assert "psutil_error=" in msg
    assert "struct.error" in msg, (
        f"struct.error mancante in psutil_error: {msg}"
    )
    assert "impossible<bad format char>" in msg


@pytest.mark.parametrize(
    ("disk_percent", "memory_percent", "expected_status", "metric", "expected_percent"),
    [
        pytest.param(95.0, 25.0, "error", "disk", 95.0, id="disk-error"),
        pytest.param(30.0, 87.0, "warn", "memory", 87.0, id="memory-warn"),
    ],
)
def test_host_resources_threshold_statuses(
    monkeypatch, disk_percent, memory_percent, expected_status, metric, expected_percent
):
    """Disk/memory thresholds keep their status mapping."""
    from app.core.admin_system_status import service

    fake_psutil = _fake_psutil(
        disk_percent=disk_percent,
        memory_percent=memory_percent,
    )
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()
    assert out["status"] == expected_status
    assert out[metric]["percent"] == expected_percent

def test_host_resources_no_loadavg_on_windows(monkeypatch):
    """psutil.getloadavg() solleva AttributeError su Windows → load_*m None,
    non crash."""
    from app.core.admin_system_status import service

    fake_psutil = _fake_psutil(loadavg=AttributeError("Windows non ha load avg"))
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    out = service.get_host_resources()
    assert out["status"] == "ok"
    assert out["cpu"]["load_1m"] is None
    assert out["cpu"]["load_5m"] is None
    assert out["cpu"]["load_15m"] is None


# get_db_pool_status


def test_db_pool_when_db_fails_returns_unknown(monkeypatch):
    from app.core.admin_system_status import service

    with patch("app.db.pg_connection.fetchone", side_effect=ConnectionError("down")):
        out = service.get_db_pool_status()

    assert out["status"] == "unknown"
    assert out["message"] == "DB non accessibile"


@pytest.mark.parametrize(
    ("row", "expected_status", "expected_percent"),
    [
        pytest.param(_db_pool_row(total=20), "ok", 20.0, id="under-warn"),
        pytest.param(_db_pool_row(total=82, active=50, idle=32), "warn", None, id="warn"),
        pytest.param(
            _db_pool_row(total=97, active=80, idle=17, waiting=2),
            "error",
            None,
            id="error",
        ),
        pytest.param(
            _db_pool_row(total=15, active=2, idle=3, idle_in_tx=10),
            "warn",
            None,
            id="idle-in-transaction",
        ),
    ],
)
def test_db_pool_threshold_statuses(monkeypatch, row, expected_status, expected_percent):
    from app.core.admin_system_status import service

    with patch("app.db.pg_connection.fetchone", return_value=row):
        out = service.get_db_pool_status()

    assert out["status"] == expected_status
    if expected_percent is not None:
        assert out["total"] == row["total"]
        assert out["percent"] == expected_percent


# get_redis_status


def test_redis_client_unavailable_returns_unknown():
    """redis non importabile -> conserva il path degraded legacy."""
    from app.core.admin_system_status import service

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "redis":
            raise ImportError("redis missing")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        out = service.get_redis_status()

    assert out["status"] == "unknown"
    assert out["message"] == "redis client non importabile"
    assert "redis missing" in out["error"]


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

    fake_redis_mod = _fake_redis_info(
        used_mb=50,
        max_mb=200,
        clients=12,
        uptime_seconds=86400 * 3,
    )
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "ok"
    assert out["percent"] == 25.0  # 50/200
    assert out["connected_clients"] == 12
    assert "uptime 3d" in out["message"]


def test_redis_over_warn_threshold(monkeypatch):
    from app.core.admin_system_status import service

    fake_redis_mod = _fake_redis_info(used_mb=80, max_mb=100)
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "warn"  # 80% >= 75%


def test_redis_without_maxmemory_has_no_percent(monkeypatch):
    """maxmemory=0 (unlimited): pct=None, status resta ok."""
    from app.core.admin_system_status import service

    fake_redis_mod = _fake_redis_info(
        used_mb=500,
        max_mb=0,
        clients=3,
        uptime_seconds=600,
    )
    monkeypatch.setitem(sys.modules, "redis", fake_redis_mod)

    out = service.get_redis_status()
    assert out["status"] == "ok"
    assert out["percent"] is None


# get_db_schema_size


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


# Aggregator include i nuovi blocchi


def test_aggregator_includes_new_blocks(monkeypatch):
    """get_system_status() ora ritorna anche host_resources, db_pool,
    redis, db_schema, jwt_blacklist."""
    from app.core.admin_system_status import service

    # Mock tutte le fonti esterne per non toccare DB/S3/redis reali
    monkeypatch.setattr(service, "get_backup_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_verify_restore_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_schedules_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_workers_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_host_resources", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_db_pool_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_redis_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_db_schema_size", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_blacklist_status", lambda: {"status": "ok", "message": "ok"})
    monkeypatch.setattr(service, "get_tickets_status", lambda: {"status": "ok", "message": "ok"})

    out = service.get_system_status()
    # Keys attese (contratto API)
    for k in ("backup", "verify_restore", "schedules", "workers",
              "host_resources", "db_pool", "redis", "db_schema",
              "jwt_blacklist", "tickets", "generated_at"):
        assert k in out, f"Mancante: {k}"


def test_router_serializes_typed_system_status(monkeypatch):
    """Router is the API serialization edge; service remains typed."""
    from app.core.admin_system_status import router, service
    from app.core.admin_system_status.models import AdminStatusBlock, SystemStatusPayload

    typed_payload = SystemStatusPayload(
        generated_at="2026-05-08T00:00:00Z",
        backup=AdminStatusBlock(status="unknown", message="S3 non accessibile", error="boom"),
        verify_restore=AdminStatusBlock(status="unknown", message="Nessuna verifica registrata", last_run_at=None),
        schedules=AdminStatusBlock(status="ok", message="Nessuno schedule attivo", total_active=0, failing_count=0, failing=[]),
        workers=AdminStatusBlock(status="error", message="Nessun worker online", nodes=0, workers_online=0),
        host_resources=AdminStatusBlock(status="unknown", message="Metriche non disponibili", disk=None, memory=None, cpu=None),
        db_pool=AdminStatusBlock(status="unknown", message="pg_stat_activity vuoto"),
        redis=AdminStatusBlock(status="error", message="Redis non raggiungibile: ConnectionError", error="down"),
        db_schema=AdminStatusBlock(status="unknown", message="DB non accessibile", error="down"),
        jwt_blacklist=AdminStatusBlock(status="unknown", message="Client Redis non inizializzato", counters={}, last_ok_epoch=None, minutes_since_last_ok=None),
        tickets=AdminStatusBlock(status="unknown", message="Query ticket fallita", error="down"),
    )
    monkeypatch.setattr(service, "get_system_status", lambda: typed_payload)

    out = router.get_system_status()

    assert isinstance(out, dict)
    assert out["backup"]["status"] == "unknown"
    assert "last_key" not in out["backup"]
    assert out["verify_restore"]["last_run_at"] is None


# get_blacklist_status — wrapper graceful su token_blacklist.get_blacklist_status


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
    # service importa il probe aliasato come `_probe` al import time:
    # patchiamo il binding sul consumer.
    monkeypatch.setattr(service, "_probe", lambda stale_threshold_seconds: expected)
    _ = tb  # noqa: F841

    out = service.get_blacklist_status()
    assert out.to_api_dict() == expected


def test_blacklist_status_unknown_when_module_missing(monkeypatch):
    """Se token_blacklist.get_blacklist_status non importabile → unknown."""
    from app.core.admin_system_status import service

    # Iniettiamo un modulo finto senza get_blacklist_status
    import sys
    import types

    fake_mod = types.ModuleType("app.shared.token_blacklist")
    # NON settiamo `get_blacklist_status` su questo modulo → ImportError
    monkeypatch.setitem(sys.modules, "app.shared.token_blacklist", fake_mod)
    # service ha gia' importato il probe come `_probe` all'import time:
    # rimuoviamo l'attribute per simulare il modulo "non importabile".
    monkeypatch.delattr(service, "_probe", raising=False)

    out = service.get_blacklist_status()
    assert out["status"] == "unknown"
    # Il wrapper degrada con messaggio generico "Probe blacklist fallita"
    # quando il probe binding non e' utilizzabile (modulo non importato o
    # symbol non risolvibile). L'osservabile contrattuale e' lo status.
    assert "probe blacklist fallita" in out["message"].lower()


def test_blacklist_status_env_threshold_passed(monkeypatch):
    """L'env var JWT_BLACKLIST_STALE_THRESHOLD_SEC viene passato al probe."""
    from app.core.admin_system_status import service

    captured = {}

    def fake_probe(stale_threshold_seconds):
        captured["threshold"] = stale_threshold_seconds
        return {"status": "ok", "message": "fake"}

    monkeypatch.setattr(service, "_probe", fake_probe)
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

    monkeypatch.setattr(service, "_probe", fake_probe)
    monkeypatch.setenv("JWT_BLACKLIST_STALE_THRESHOLD_SEC", "garbage")

    service.get_blacklist_status()
    assert captured["threshold"] == 1800


# get_tickets_status — graceful + soglie severity


@pytest.mark.parametrize(
    ("total", "counts", "expected_status", "message_part"),
    [
        pytest.param(3, _ticket_counts(low=1, medium=2), "ok", None, id="no-critical"),
        pytest.param(0, _ticket_counts(), "ok", "nessun ticket", id="zero"),
        pytest.param(5, _ticket_counts(medium=2, high=2, critical=1), "warn", None, id="some-critical"),
        pytest.param(8, _ticket_counts(high=8), "warn", None, id="many-high"),
        pytest.param(
            10,
            _ticket_counts(medium=1, high=2, critical=3),
            "error",
            "3 ticket critici",
            id="many-critical",
        ),
    ],
)
def test_tickets_status_thresholds(monkeypatch, total, counts, expected_status, message_part):
    from app.core.admin_system_status import service
    from app.core.admin_system_status import tickets_metrics

    monkeypatch.setattr(
        service,
        "get_active_ticket_metrics",
        lambda: tickets_metrics.ActiveTicketMetrics(
            total_active=total,
            by_severity=counts,
        ),
    )

    out = service.get_tickets_status()
    assert out["status"] == expected_status
    if total == 3:
        assert out["total_active"] == 3
        assert out["by_severity"]["critical"] == 0
    if message_part:
        assert message_part in out["message"].lower()

def test_tickets_status_unknown_on_db_error(monkeypatch):
    from app.core.admin_system_status import service
    from app.core.admin_system_status import tickets_metrics

    def boom():
        raise RuntimeError("db down")

    _ = tickets_metrics  # noqa: F841
    monkeypatch.setattr(service, "get_active_ticket_metrics", boom)

    out = service.get_tickets_status()
    assert out["status"] == "unknown"
