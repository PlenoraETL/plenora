"""
Contract test — `DefaultEngineFactory` MSSQL `fast_executemany`.

`fast_executemany=True` su pyodbc/MSSQL trasforma l'executemany standard
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def test_mssql_engine_default_enables_fast_executemany(monkeypatch):
    """Default (no env): fast_executemany=True passato a create_engine."""
    monkeypatch.delenv("MSSQL_FAST_EXECUTEMANY", raising=False)

    from app.core.query_execution.query_engines import DefaultEngineFactory

    factory = DefaultEngineFactory()

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        eng = MagicMock()
        # Mock connect/execute per il test query "SELECT 1"
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        eng.connect.return_value = ctx
        return eng

    with patch(
        "app.core.query_execution.query_engines.create_engine",
        side_effect=fake_create_engine,
    ):
        factory._crea_engine_mssql(
            "mssql+pyodbc://...?driver=ODBC+Driver+18+for+SQL+Server",
            connect_args={},
        )

    assert captured["kwargs"].get("fast_executemany") is True


@pytest.mark.parametrize("val", ["off", "false", "0", "no", "disabled", "OFF"])
def test_mssql_engine_env_off_disables_fast_executemany(monkeypatch, val):
    monkeypatch.setenv("MSSQL_FAST_EXECUTEMANY", val)

    from app.core.query_execution.query_engines import DefaultEngineFactory
    factory = DefaultEngineFactory()

    captured = {}

    def fake_create_engine(url, **kwargs):
        captured["kwargs"] = kwargs
        eng = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        eng.connect.return_value = ctx
        return eng

    with patch(
        "app.core.query_execution.query_engines.create_engine",
        side_effect=fake_create_engine,
    ):
        factory._crea_engine_mssql("mssql+pyodbc://...", {})

    assert "fast_executemany" not in captured["kwargs"]


def test_mssql_driver_17_fallback_preserves_fast_executemany(monkeypatch):
    """Quando il fallback Driver 18 → 17 scatta, fast_executemany=True
    deve essere propagato anche al secondo create_engine."""
    monkeypatch.delenv("MSSQL_FAST_EXECUTEMANY", raising=False)

    from app.core.query_execution.query_engines import DefaultEngineFactory
    from app.core.query_execution import query_engines as module
    factory = DefaultEngineFactory()
    monkeypatch.setattr(
        module,
        "_installed_mssql_drivers",
        lambda: (
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
        ),
    )

    calls = []

    def fake_create_engine(url, **kwargs):
        calls.append({"url": url, "kwargs": kwargs})
        # Primo tentativo (Driver 18) fallisce
        if "ODBC+Driver+18" in url and len(calls) == 1:
            raise RuntimeError("ODBC Driver 18 for SQL Server not found")
        eng = MagicMock()
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=ctx)
        ctx.__exit__ = MagicMock(return_value=False)
        eng.connect.return_value = ctx
        return eng

    with patch(
        "app.core.query_execution.query_engines.create_engine",
        side_effect=fake_create_engine,
    ):
        factory._crea_engine_mssql(
            "mssql+pyodbc://x?driver=ODBC+Driver+18+for+SQL+Server",
            connect_args={},
        )

    assert len(calls) == 2
    # Entrambi i tentativi devono includere fast_executemany
    assert calls[0]["kwargs"].get("fast_executemany") is True
    assert calls[1]["kwargs"].get("fast_executemany") is True
    # Il secondo deve essere Driver 17
    assert "ODBC+Driver+17" in calls[1]["url"]


def test_non_driver18_error_not_caught(monkeypatch):
    """Errori che NON sono "Driver 18 mancante" devono propagare,
    non triggerare il fallback (regression guard)."""
    monkeypatch.delenv("MSSQL_FAST_EXECUTEMANY", raising=False)
    from app.core.query_execution.query_engines import DefaultEngineFactory
    factory = DefaultEngineFactory()

    def fake_create_engine(url, **kwargs):
        raise RuntimeError("Login failed for user 'sa'")

    with patch(
        "app.core.query_execution.query_engines.create_engine",
        side_effect=fake_create_engine,
    ):
        with pytest.raises(RuntimeError, match="Login failed"):
            factory._crea_engine_mssql(
                "mssql+pyodbc://x?driver=ODBC+Driver+18", {},
            )


def test_driver18_server_error_does_not_fallback_to_missing_driver17(
    monkeypatch,
):
    """Il nome del driver nel messaggio non implica che manchi localmente."""
    from app.core.query_execution import query_engines as module
    from app.core.query_execution.query_engines import DefaultEngineFactory

    monkeypatch.setattr(
        module,
        "_installed_mssql_drivers",
        lambda: (
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
        ),
    )
    calls: list[str] = []

    def fake_create_engine(url, **_kwargs):
        calls.append(url)
        raise RuntimeError(
            "[08001] [Microsoft][ODBC Driver 18 for SQL Server] "
            "TCP Provider: connection refused"
        )

    with patch(
        "app.core.query_execution.query_engines.create_engine",
        side_effect=fake_create_engine,
    ):
        with pytest.raises(RuntimeError, match="connection refused"):
            DefaultEngineFactory()._crea_engine_mssql(
                "mssql+pyodbc://x?driver=ODBC+Driver+18+for+SQL+Server",
                {},
            )

    assert len(calls) == 1
    assert "Driver+17" not in calls[0]


def test_connection_url_uses_the_installed_driver(monkeypatch):
    from types import SimpleNamespace

    from app.core.query_execution import query_engines as module
    from app.core.query_execution.query_engines import DefaultEngineFactory

    monkeypatch.setattr(
        module,
        "_installed_mssql_drivers",
        lambda: ("ODBC Driver 17 for SQL Server",),
    )
    conn = SimpleNamespace(
        type="mssql",
        host="sql",
        port=1433,
        database="db",
        username="user",
        password="secret",
    )

    url = DefaultEngineFactory()._costruisci_connection_string(conn)
    assert "ODBC+Driver+17+for+SQL+Server" in url
    assert "ODBC+Driver+18" not in url
