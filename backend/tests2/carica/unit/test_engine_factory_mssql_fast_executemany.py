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

    from app.db.query_executor import DefaultEngineFactory

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
        "app.db.query_executor.create_engine",
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

    from app.db.query_executor import DefaultEngineFactory
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
        "app.db.query_executor.create_engine",
        side_effect=fake_create_engine,
    ):
        factory._crea_engine_mssql("mssql+pyodbc://...", {})

    assert "fast_executemany" not in captured["kwargs"]


def test_mssql_driver_17_fallback_preserves_fast_executemany(monkeypatch):
    """Quando il fallback Driver 18 → 17 scatta, fast_executemany=True
    deve essere propagato anche al secondo create_engine."""
    monkeypatch.delenv("MSSQL_FAST_EXECUTEMANY", raising=False)

    from app.db.query_executor import DefaultEngineFactory
    factory = DefaultEngineFactory()

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
        "app.db.query_executor.create_engine",
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
    from app.db.query_executor import DefaultEngineFactory
    factory = DefaultEngineFactory()

    def fake_create_engine(url, **kwargs):
        raise RuntimeError("Login failed for user 'sa'")

    with patch(
        "app.db.query_executor.create_engine", side_effect=fake_create_engine,
    ):
        with pytest.raises(RuntimeError, match="Login failed"):
            factory._crea_engine_mssql(
                "mssql+pyodbc://x?driver=ODBC+Driver+18", {},
            )
