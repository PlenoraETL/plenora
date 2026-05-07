"""
Provides pytest fixtures for unit tests.

Centralizes local pytest setup for the surrounding test package.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_not_esecutore
from app.core.intermediate.router import router


@pytest.fixture
def admin_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def superadmin_user() -> CurrentUser:
    return CurrentUser(
        user_id="root-1",
        username="root",
        group_id="default",
        role="superadmin",
    )


def build_client(user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_not_esecutore] = lambda: user
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)
