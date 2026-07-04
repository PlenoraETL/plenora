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
from tests2.functional.harness.env import configure_broker_env, isolate_task_queues
from tests2.functional.harness.workers import spawn_worker
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
    env["ENVIRONMENT"] = "development"
    env["JWT_SECRET_KEY"] = "tests2-functional-jwt-secret"
    env["PLENORA_ENCRYPTION_KEY"] = "tests2-functional-encryption-key"
    env["DATA_DIR"] = str(data_dir)
    configure_broker_env(env)
    # Code dedicate per-test (functional_env e' function-scoped): il worker
    # spawnato consuma SOLO i task di questo test — rimpiazza sia il vecchio
    # isolamento redis-DB sia il flush per-test.
    isolate_task_queues(env)
    # Opt-in SSRF loopback: i test funzionali lanciano un fake HTTP
    # server su 127.0.0.1 e poi fanno chiamare il backend reale verso
    # quell'URL. Senza questa opt-in, `validate_external_url` blocca
    # il loopback come SSRF (corretto in prod, falso positivo qui).
    # Canonical PLENORA_* (alias retro-compat URBY_* lo stesso valore,
    # rinominato 2026-04-23 / P1.5).
    env["PLENORA_ALLOW_LOOPBACK_OUTBOUND"] = "1"
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
    wait_for_http_ready(f"{url}/health", timeout_s=20.0, process=proc)
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
    wait_for_http_ready(f"{url}/", timeout_s=30.0, process=proc)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture
def task_worker(functional_env):
    """Avvia un worker Dramatiq locale per i functional async-lite.

    Isolamento: le code sono suffissate per-test (isolate_task_queues in
    functional_env), quindi niente flush del broker — i task orfani di
    run precedenti vivono su code diverse senza consumer."""
    proc = spawn_worker(
        workdir=FUNCTIONAL_ROOT.parent.parent,
        env=functional_env,
        loglevel="warning",
    )
    try:
        yield {"process": proc}
    finally:
        stop_process(proc)


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


# Auto-marker: requires_backend_http_service
#
# Marca i test che dipendono direttamente o transitivamente da una fixture
# che spawna un backend HTTP live (uvicorn). Permette il deselect dalla CI
# principale push/PR via `-m "not requires_backend_http_service"`. Vedi
# tests2/pytest.ini per la definizione del marker e
# .github/workflows/ci.yml per l'opt-out a livello workflow.
#
# I test marcati restano vivi nel codebase: gireranno in workflow tier
# functional/manual/nightly separato (Binario B-2, follow-up).
_HTTP_FIXTURES = frozenset({
    "backend_server",
    "fake_api_server",
    "auth_token",
})


# Fixture che avviano servizi reali (backend HTTP, fake-API, worker
# Dramatiq): i test che li usano richiedono lo stack e2e-v2 e non possono
# girare nel run unit di default.
_LIVE_SERVICE_FIXTURES = _HTTP_FIXTURES | frozenset({"task_worker"})


def pytest_collection_modifyitems(config, items):
    run_live = os.getenv("URBY_RUN_INTEGRATION") == "1"
    for item in items:
        fixturenames = set(item.fixturenames)
        if _HTTP_FIXTURES & fixturenames:
            item.add_marker(pytest.mark.requires_backend_http_service)
        # Gate: senza servizi reali (default) questi test ERRORavano al boot
        # delle fixture; ora SKIPpano puliti. Per eseguirli: URBY_RUN_INTEGRATION=1
        # con lo stack backend/test-e2e-v2 attivo.
        if not run_live and (_LIVE_SERVICE_FIXTURES & fixturenames):
            item.add_marker(pytest.mark.skip(
                reason="richiede servizi reali (e2e-v2). "
                       "Esegui con URBY_RUN_INTEGRATION=1."
            ))
