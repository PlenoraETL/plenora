"""
Provides pytest fixtures for unit tests.

Centralizes local pytest setup for the surrounding test package.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import get_current_user
from app.core.orchestrator.router import tasks_router


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id="u-1",
        username="tester",
        group_id="grp-1",
        role="admin",
    )


@pytest.fixture
def client(current_user: CurrentUser) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.include_router(tasks_router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)
