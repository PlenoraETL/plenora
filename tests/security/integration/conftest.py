import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest


def _reset_rate_limit():
    """Resetta il rate limiter in memoria del backend."""
    try:
        from app.core.auth.auth import _failed_attempts, _rate_lock
        with _rate_lock:
            _failed_attempts.clear()
    except Exception:
        pass


@pytest.fixture(autouse=True, scope="session")
def reset_rate_limit_before_session():
    """Resetta il rate limiter all'inizio della sessione integration."""
    _reset_rate_limit()
    yield


@pytest.fixture(autouse=True)
def reset_rate_limit_before_each():
    """Resetta il rate limiter prima di ogni test — evita 429 durante il full run."""
    _reset_rate_limit()
    yield
