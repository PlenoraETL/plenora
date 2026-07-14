"""Security and public-shape contracts for the runtime storage explorer."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.admin_object_storage import router as router_module
from app.core.auth.dependencies import require_superadmin
from app.shared.contracts.auth import CurrentUser


class _Storage:
    bucket = "runtime-test"

    def list_objects_page(self, prefix, *, limit, cursor=None):
        return {
            "objects": [
                {
                    "key": "tenants/g1/results/p1/r1.bin",
                    "size": 7,
                    "last_modified": datetime.now(timezone.utc),
                }
            ],
            "next_cursor": None,
        }


def _app(*, authenticated: bool) -> TestClient:
    app = FastAPI()
    if authenticated:
        app.dependency_overrides[require_superadmin] = lambda: CurrentUser(
            user_id="root",
            username="root",
            group_id="g1",
            role="superadmin",
        )
    app.include_router(router_module.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_catalog_requires_authentication() -> None:
    response = _app(authenticated=False).get("/api/admin/object-storage")
    assert response.status_code in {401, 403}


def test_superadmin_catalog_returns_tenant_metadata(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.admin_object_storage.service.get_runtime_object_storage",
        lambda: _Storage(),
    )

    response = _app(authenticated=True).get("/api/admin/object-storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["bucket"] == "runtime-test"
    assert payload["objects"][0]["tenant_group_id"] == "g1"
    assert payload["objects"][0]["domain"] == "results"


def test_download_rejects_keys_outside_runtime_namespace(monkeypatch) -> None:
    monkeypatch.setattr(
        router_module,
        "get_runtime_object_storage",
        lambda: (_ for _ in ()).throw(AssertionError("storage must not open")),
    )

    response = _app(authenticated=True).get(
        "/api/admin/object-storage/download",
        params={"key": "backups/database.dump"},
    )

    assert response.status_code == 400
