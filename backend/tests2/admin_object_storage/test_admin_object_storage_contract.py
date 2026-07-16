"""Security and public-shape contracts for the runtime storage explorer."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.admin_object_storage import router as router_module
from app.core.admin_object_storage import service as storage_service
from app.core.auth.dependencies import require_superadmin
from app.core.results.db import ResultMetadataRow
from app.shared.contracts.auth import CurrentUser


class _Storage:
    bucket = "runtime-test"

    def __init__(self):
        self.deleted: list[str] = []

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

    def delete(self, key: str) -> None:
        self.deleted.append(key)


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


def test_project_results_catalog_is_grouped(monkeypatch) -> None:
    monkeypatch.setattr(
        router_module,
        "list_project_results",
        lambda group_id=None: {
            "projects": [
                {
                    "project_id": "p1",
                    "project_name": "Immobili",
                    "project_type": "estrai",
                    "group_id": group_id or "g1",
                    "results": [
                        {
                            "id": "r1",
                            "key": "tenants/g1/results/p1/r1.bin",
                            "filename": "immobili.xlsx",
                            "format": "xlsx",
                            "size_bytes": 10,
                            "rows_count": 3,
                            "created_at": datetime.now(timezone.utc),
                        }
                    ],
                }
            ],
            "results_count": 1,
            "size_bytes": 10,
        },
    )

    response = _app(authenticated=True).get(
        "/api/admin/object-storage/projects",
        params={"group_id": "g1"},
    )

    assert response.status_code == 200
    assert response.json()["projects"][0]["results"][0]["filename"] == "immobili.xlsx"


def test_project_results_service_keeps_tenant_project_names_separate(monkeypatch) -> None:
    created = datetime.now(timezone.utc)
    monkeypatch.setattr(
        storage_service.projects_db,
        "list_projects",
        lambda group_id=None: [
            SimpleNamespace(id="shared", group_id="g1", name="Uno", project_type="estrai"),
            SimpleNamespace(id="shared", group_id="g2", name="Due", project_type="manipola"),
        ],
    )
    monkeypatch.setattr(
        storage_service.results_db,
        "list_admin_result_rows",
        lambda group_id=None: [
            ResultMetadataRow(
                id="r1", project_id="shared", group_id="g1",
                filename="uno.xlsx", format="xlsx", size_bytes=10,
                rows_count=1, created_at=created,
                storage_key="tenants/g1/results/shared/r1.bin",
            ),
            ResultMetadataRow(
                id="r2", project_id="shared", group_id="g2",
                filename="due.csv", format="csv", size_bytes=20,
                rows_count=2, created_at=created,
                storage_key="tenants/g2/results/shared/r2.bin",
            ),
        ],
    )

    catalog = storage_service.list_project_results()

    assert {(item.group_id, item.project_name) for item in catalog.projects} == {
        ("g1", "Uno"),
        ("g2", "Due"),
    }
    assert catalog.results_count == 2
    assert catalog.size_bytes == 30


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


def test_delete_requires_authentication() -> None:
    response = _app(authenticated=False).delete(
        "/api/admin/object-storage",
        params={"key": "tenants/g1/results/p1/r1.bin"},
    )
    assert response.status_code in {401, 403}


def test_superadmin_can_delete_one_exact_runtime_object(monkeypatch) -> None:
    storage = _Storage()
    monkeypatch.setattr(router_module, "get_runtime_object_storage", lambda: storage)

    key = "tenants/g1/results/p1/r1.bin"
    response = _app(authenticated=True).delete(
        "/api/admin/object-storage",
        params={"key": key},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True, "key": key}
    assert storage.deleted == [key]


def test_delete_rejects_namespace_root_without_calling_storage(monkeypatch) -> None:
    storage = _Storage()
    monkeypatch.setattr(router_module, "get_runtime_object_storage", lambda: storage)

    response = _app(authenticated=True).delete(
        "/api/admin/object-storage",
        params={"key": "tenants/g1/results"},
    )

    assert response.status_code == 400
    assert storage.deleted == []


def test_delete_rejects_key_outside_runtime_namespace(monkeypatch) -> None:
    storage = _Storage()
    monkeypatch.setattr(router_module, "get_runtime_object_storage", lambda: storage)

    response = _app(authenticated=True).delete(
        "/api/admin/object-storage",
        params={"key": "backups/database.dump"},
    )

    assert response.status_code == 400
    assert storage.deleted == []


def test_delete_all_previews_only_deletes_preview_domain(monkeypatch) -> None:
    class PreviewStorage(_Storage):
        def list_objects_page(self, prefix, *, limit, cursor=None):
            assert prefix == "tenants/"
            return {
                "objects": [
                    {
                        "key": "tenants/g1/previews/p1/t1.json",
                        "size": 7,
                        "last_modified": datetime.now(timezone.utc),
                    },
                    {
                        "key": "tenants/g1/results/p1/r1.bin",
                        "size": 7,
                        "last_modified": datetime.now(timezone.utc),
                    },
                ],
                "next_cursor": None,
            }

    storage = PreviewStorage()
    monkeypatch.setattr(
        "app.core.admin_object_storage.service.get_runtime_object_storage",
        lambda: storage,
    )

    response = _app(authenticated=True).delete(
        "/api/admin/object-storage/previews",
        params={"scope": "canonical"},
    )

    assert response.status_code == 200
    assert response.json()["deleted_count"] == 1
    assert storage.deleted == ["tenants/g1/previews/p1/t1.json"]
