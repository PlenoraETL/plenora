from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.shared.contracts.auth import CurrentUser
from app.core.auth.auth import require_not_esecutore
from app.core.features.routes import router


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
    app.dependency_overrides[require_not_esecutore] = lambda: current_user
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fixed_now() -> datetime:
    return datetime(2026, 4, 13, tzinfo=timezone.utc)
