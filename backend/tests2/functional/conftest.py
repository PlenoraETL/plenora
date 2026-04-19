"""Fixture condivise per i test funzionali."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from tests2.functional.harness.auth import (
    cleanup_functional_user,
    login_functional_user,
    seed_functional_user,
)
from tests2.functional.harness.celery import spawn_celery_worker
from tests2.functional.harness.celery import flush_redis_db
from tests2.functional.harness.ports import find_free_port
from tests2.functional.harness.processes import (
    spawn_process,
    stop_process,
    wait_for_http_ready,
)


FUNCTIONAL_ROOT = Path(__file__).resolve().parent


@pytest.fixture
def functional_env(tmp_path) -> dict:
    """Env base per processi funzionali locali."""
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    env.setdefault("ENVIRONMENT", "development")
    env["JWT_SECRET_KEY"] = "tests2-functional-jwt-secret"
    env["DATA_DIR"] = str(data_dir)
    env["CELERY_BROKER_URL"] = "redis://127.0.0.1:6379/0"
    env["CELERY_RESULT_BACKEND"] = "redis://127.0.0.1:6379/1"
    # Opt-in SSRF loopback: i test funzionali lanciano un fake HTTP
    # server su 127.0.0.1 e poi fanno chiamare il backend reale verso
    # quell'URL. Senza questa opt-in, `validate_external_url` blocca
    # il loopback come SSRF (corretto in prod, falso positivo qui).
    env["URBY_ALLOW_LOOPBACK_OUTBOUND"] = "1"
    return env


@pytest.fixture
def fake_api_server(functional_env):
    """Avvia il fake HTTP server per i test funzionali di api_connector."""
    host = "127.0.0.1"
    port = find_free_port(host)
    url = f"http://{host}:{port}"
    proc = spawn_process(
        name="fake-api",
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
        workdir=FUNCTIONAL_ROOT.parent.parent,
        env=functional_env,
    )
    wait_for_http_ready(f"{url}/health", timeout_s=20.0)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture
def backend_server(functional_env):
    """Avvia il backend FastAPI reale su una porta libera."""
    host = "127.0.0.1"
    port = find_free_port(host)
    url = f"http://{host}:{port}"
    proc = spawn_process(
        name="backend",
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
        workdir=FUNCTIONAL_ROOT.parent.parent,
        env=functional_env,
    )
    wait_for_http_ready(f"{url}/", timeout_s=30.0)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture
def celery_worker(functional_env):
    """Avvia un worker Celery locale per i functional async-lite."""
    broker_url = functional_env.get("CELERY_BROKER_URL") or functional_env.get("REDIS_URL")
    result_backend = functional_env.get("CELERY_RESULT_BACKEND")
    if broker_url:
        flush_redis_db(broker_url)
    if result_backend:
        flush_redis_db(result_backend)
    proc = spawn_celery_worker(
        workdir=FUNCTIONAL_ROOT.parent.parent,
        env=functional_env,
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
def _functional_flush_redis_per_test(functional_env):
    """Isolamento per test anche con worker/backend module-scoped."""
    broker_url = functional_env.get("CELERY_BROKER_URL")
    result_backend = functional_env.get("CELERY_RESULT_BACKEND")
    try:
        yield
    finally:
        if broker_url:
            flush_redis_db(broker_url)
        if result_backend:
            flush_redis_db(result_backend)


@pytest.fixture
def functional_user_seed():
    """Seed DB isolato: gruppo + utente admin per login reale."""
    seed = seed_functional_user(role="admin")
    try:
        yield seed
    finally:
        cleanup_functional_user(seed)


@pytest.fixture
def auth_token(backend_server, functional_user_seed):
    """JWT reale ottenuto via /api/auth/login."""
    payload = login_functional_user(
        backend_server["base_url"],
        username=functional_user_seed["username"],
        password=functional_user_seed["password"],
    )
    return payload["access_token"]


@pytest.fixture
def auth_headers(auth_token):
    return {"Authorization": f"Bearer {auth_token}"}
