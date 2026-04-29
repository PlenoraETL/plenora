"""Fixture condivise per gli integration test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.core.auth.auth import hash_password
from app.db.config_db_service import create_group, create_user, delete_group_cascade

from tests2.functional.harness.celery import spawn_celery_worker
from tests2.functional.harness.celery import flush_redis_db
from tests2.functional.harness.http import http_json_response
from tests2.functional.harness.ports import find_free_port
from tests2.functional.harness.processes import (
    spawn_process,
    stop_process,
    wait_for_http_ready,
)
from tests2.integration.harness.db import make_it_id, pg_execute, pg_fetchone


INTEGRATION_ROOT = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def integration_env(tmp_path_factory) -> dict:
    """Env esplicito per backend reale contro container Postgres/Redis."""
    data_dir = tmp_path_factory.mktemp("tests2-integration-data") / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    env["ENVIRONMENT"] = "development"
    env["JWT_SECRET_KEY"] = "tests2-integration-jwt-secret"
    # DB: usa la configurazione gia' funzionante del progetto (.env/defaults).
    # I tentativi di forzare manualmente il DSN dei container hanno mostrato
    # uno schema non inizializzato rispetto al backend reale.
    env.pop("DATABASE_URL", None)
    env.pop("DB_SCHEMA", None)
    env["CELERY_BROKER_URL"] = "redis://127.0.0.1:6379/0"
    env["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6379/1"
    env["DATA_DIR"] = str(data_dir)
    return env


@pytest.fixture(scope="module")
def integration_backend_server(integration_env):
    host = "127.0.0.1"
    port = find_free_port(host)
    url = f"http://{host}:{port}"
    proc = spawn_process(
        name="integration-backend",
        command=[
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        workdir=INTEGRATION_ROOT.parent.parent,
        env=integration_env,
    )
    wait_for_http_ready(f"{url}/", timeout_s=30.0)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture(scope="module")
def integration_fake_api_server(integration_env):
    """Fake API esterna locale per integration test di api_connector."""
    host = "127.0.0.1"
    port = find_free_port(host)
    url = f"http://{host}:{port}"
    proc = spawn_process(
        name="integration-fake-api",
        command=[
            sys.executable,
            "-m",
            "uvicorn",
            "tests2.functional.fakes.api_connector_fake:app",
            "--host",
            host,
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        workdir=INTEGRATION_ROOT.parent.parent,
        env=integration_env,
    )
    wait_for_http_ready(f"{url}/health", timeout_s=20.0)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture(scope="module")
def integration_celery_worker(integration_env):
    """Worker Celery reale contro Redis dei container di test."""
    broker_url = integration_env.get("CELERY_BROKER_URL")
    result_backend = integration_env.get("CELERY_RESULT_BACKEND")
    if broker_url:
        flush_redis_db(broker_url)
    if result_backend:
        flush_redis_db(result_backend)
    proc = spawn_celery_worker(
        workdir=INTEGRATION_ROOT.parent.parent,
        env=integration_env,
        loglevel="warning",
    )
    try:
        yield {"process": proc}
    finally:
        stop_process(proc)
        if broker_url:
            flush_redis_db(broker_url)
        if result_backend:
            flush_redis_db(result_backend)


@pytest.fixture(autouse=True)
def _integration_flush_redis_per_test(integration_env):
    """Isolamento per test anche con worker/backend module-scoped."""
    broker_url = integration_env.get("CELERY_BROKER_URL")
    result_backend = integration_env.get("CELERY_RESULT_BACKEND")
    try:
        yield
    finally:
        if broker_url:
            flush_redis_db(broker_url)
        if result_backend:
            flush_redis_db(result_backend)


@pytest.fixture
def integration_user_seed(integration_backend_server):
    """Seed su Postgres reale con cleanup mirato a fine test."""
    _ = integration_backend_server
    group_id = make_it_id("grp")
    user_id = make_it_id("user")
    username = make_it_id("usernm")
    password = "IntegrationPass123!"

    create_group(group_id, f"Integration {group_id}", "tests2 integration")
    create_user(
        user_id=user_id,
        username=username,
        password_hash=hash_password(password),
        group_id=group_id,
        role="admin",
        email=f"{username}@example.test",
    )

    seed = {
        "group_id": group_id,
        "user_id": user_id,
        "username": username,
        "password": password,
        "role": "admin",
    }
    try:
        yield seed
    finally:
        delete_group_cascade(group_id)


@pytest.fixture
def integration_auth_headers(integration_backend_server, integration_user_seed):
    status, body, _ = http_json_response(
        f"{integration_backend_server['base_url']}/api/auth/login",
        method="POST",
        payload={
            "username": integration_user_seed["username"],
            "password": integration_user_seed["password"],
        },
        timeout=10.0,
    )
    if status != 200:
        raise RuntimeError(f"Integration login fallito: HTTP {status} - {body}")
    return {"Authorization": f"Bearer {body['access_token']}"}


@pytest.fixture
def integration_db_probe():
    return {
        "fetchone": pg_fetchone,
        "execute": pg_execute,
        "make_id": make_it_id,
    }


# =============================================================================
# Auto-marker: requires_backend_http_service
# =============================================================================
#
# Marca i test che dipendono direttamente o transitivamente da una fixture
# che spawna un backend HTTP live (uvicorn) per i test integration. Vedi
# nota analoga in tests2/functional/conftest.py per dettagli.
_HTTP_FIXTURES = frozenset({
    "integration_backend_server",
    "integration_fake_api_server",
    "integration_user_seed",
    "integration_auth_headers",
})


def pytest_collection_modifyitems(config, items):
    for item in items:
        if _HTTP_FIXTURES & set(item.fixturenames):
            item.add_marker(pytest.mark.requires_backend_http_service)
