"""
tests/error_handling/unit/conftest.py
=======================================
Fixture per test unitari ERR (no backend, no DB reale).
"""

import sys
sys.path.insert(0, r"C:\Users\Marco\Desktop\int_efm\URBY-ETL\backend")

import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_current_user():
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="uid-admin", username="admin",
                       group_id="default", role="admin")


@pytest.fixture
def mock_superadmin():
    from app.core.auth.auth import CurrentUser
    return CurrentUser(user_id="uid-super", username="superadmin",
                       group_id="default", role="superadmin")


@pytest.fixture
def auth_override(mock_current_user):
    from app.core.auth.auth import get_current_user
    return {get_current_user: lambda: mock_current_user}


@pytest.fixture
def superadmin_override(mock_superadmin):
    from app.core.auth.auth import get_current_user
    return {get_current_user: lambda: mock_superadmin}


def make_test_client(router, overrides=None):
    """Helper: crea TestClient con router e dependency override."""
    app = FastAPI()
    if overrides:
        app.dependency_overrides.update(overrides)
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)
