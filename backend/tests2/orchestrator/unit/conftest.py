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


@pytest.fixture(autouse=True)
def _reset_orchestrator_module_singleton():
    """Salva/ripristina `router._orchestrator` attorno a ogni test.

    Alcuni test inizializzano il router con `init_orchestrator(MagicMock(...))`
    senza ripristino: il global restava sporco per il resto del processo e i
    test successivi (es. breadcrumb full-cleanup) leggevano il project_service
    del mock stantio invece del fallback, mancando i patch di classe."""
    import app.core.orchestrator.router as router_mod

    original = router_mod._orchestrator
    yield
    router_mod._orchestrator = original


@pytest.fixture(autouse=True)
def _task_cleanup_without_db(monkeypatch):
    """full-cleanup senza DB: il controllo «niente da pulire» (che legge
    task_results e metadati) dice di no, le richieste di pulizia annotate sul
    task stanno in memoria, i metadati si tolgono senza DB. I test che
    blindano quelle regole le provano direttamente
    (tests2/orchestrator/unit/test_full_cleanup_order_contract.py)."""
    from app.core.orchestrator import task_cleanup
    from app.core.orchestrator import task_service as task_service_module
    from app.shared.kv import InMemoryKeyValueStore

    monkeypatch.setattr(task_cleanup, "nothing_left", lambda task_id: False)
    monkeypatch.setattr(task_cleanup, "_requests", InMemoryKeyValueStore())
    monkeypatch.setattr(task_service_module.task_service.metadata_store, "delete", lambda task_id: True)


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
