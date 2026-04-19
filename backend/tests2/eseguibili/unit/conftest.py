import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth.auth import CurrentUser, get_current_user
from app.core.eseguibili.router import router


@pytest.fixture
def current_user() -> CurrentUser:
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
    app.dependency_overrides[get_current_user] = lambda: user
    app.include_router(router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)
