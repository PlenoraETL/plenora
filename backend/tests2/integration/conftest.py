"""Fixture condivise per gli integration test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from app.core.auth.auth import hash_password
from app.core.auth.db import create_group, create_user
from app.core.auth.group_cleanup import delete_group_cascade

from tests2.functional.harness.env import configure_broker_env, isolate_task_queues
from tests2.functional.harness.workers import spawn_worker
from tests2.functional.harness.http import http_json_response
from tests2.functional.harness.ports import find_free_port
from tests2.functional.harness.processes import (
    spawn_process,
    stop_process,
    wait_for_http_ready,
)
from tests2.integration.harness.db import make_it_id, pg_execute, pg_fetchone


INTEGRATION_ROOT = Path(__file__).resolve().parent

_BACKEND_STACK_FIXTURES = frozenset({
    "integration_backend_server",
    "integration_task_worker",
    "integration_fake_api_server",
    "integration_user_seed",
    "integration_auth_headers",
})

_TIER1_INSTALL_VALIDATION_FILES = frozenset({
    "test_api_connection_external_roundtrip.py",
    "test_auth_login_db_roundtrip.py",
    "test_http_response_shape_contract.py",
    "test_jwt_logout_blacklist_integration.py",
    "test_login_rate_limit_integration.py",
    "test_projects_canvas_roundtrip.py",
    "test_task_api_async_contract.py",
})

_DEVELOPER_NIGHTLY_FILES = frozenset({
    "test_arricchisci_file_source_execute_roundtrip.py",
    "test_canvas_result_columns_roundtrip.py",
    "test_manipola_formula_roundtrip.py",
    "test_project_source_cross_tenant_contract.py",
    "test_project_source_latest_artifact_roundtrip.py",
    "test_project_source_missing_latest_contract.py",
    "test_replace_atomic.py",
    "test_rollback_regression_write_modes.py",
    "test_scheduler_failure_contract.py",
    "test_scheduler_manual_run_roundtrip.py",
    "test_task_download_result_contract.py",
})

_OPTIONAL_PROVIDER_PREFIX_MARKERS = (
    ("test_arcgis_", ("requires_arcgis", "requires_external_network")),
    ("test_mariadb_", ("requires_mariadb",)),
    ("test_mssql_", ("requires_mssql",)),
    ("test_mysql_", ("requires_mysql",)),
    ("test_oracle_", ("requires_oracle",)),
    ("test_sqlserver_", ("requires_mssql",)),
)

_POSTGRES_PROVIDER_FILES = frozenset({
    "test_pg_reconciliation_smoke.py",
    "test_postgres_carica_roundtrip.py",
    "test_postgres_carica_stage_swap.py",
    "test_postgres_connection_and_estrai_preview.py",
    "test_postgres_execute_download_roundtrip.py",
    "test_postgres_pipeline_carica_chain.py",
})

_MIXED_OPTIONAL_PROVIDER_MARKERS = {
    "test_replace_atomic.py": ("requires_oracle", "requires_postgres"),
    "test_rollback_regression_write_modes.py": ("requires_oracle", "requires_postgres"),
}


@pytest.fixture(scope="module")
def integration_env(tmp_path_factory) -> dict:
    """Env esplicito per backend reale contro container Postgres/RabbitMQ."""
    data_dir = tmp_path_factory.mktemp("tests2-integration-data") / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd()
    env["ENVIRONMENT"] = "development"
    env["JWT_SECRET_KEY"] = "tests2-integration-jwt-secret"
    env["PLENORA_ENCRYPTION_KEY"] = "tests2-integration-encryption-key"
    # DATABASE_URL: in CI il workflow ci-functional.yml lo fornisce
    # esplicitamente per il subprocess uvicorn (B-2 tier). In dev locale
    # il subprocess legge .env via app.config dotenv import-time.
    # Preserviamo il valore se gia' impostato dall'ambiente (CI scenario),
    # altrimenti il subprocess si appoggia al .env di progetto.
    # Storia: prima il pop era incondizionato (per impedire shadow di
    # valori shell stale), ma rompeva il subprocess in CI ephemeral
    # senza .env, generando RuntimeError("DATABASE_URL non configurato")
    # in pg_connection.py:113 e uvicorn exit code 1 al boot.
    # Se in dev locale hai DATABASE_URL nell'env shell e vuoi forzare
    # l'uso di .env, rimuovi DATABASE_URL dall'ambiente shell prima di
    # eseguire pytest.
    # DB_SCHEMA resta poppato per usare il default 'plenora'.
    env.pop("DB_SCHEMA", None)
    configure_broker_env(env)
    # Code dedicate per-modulo (integration_env e' module-scoped): il worker
    # spawnato consuma solo i task di questo modulo — rimpiazza il vecchio
    # isolamento redis-DB (11/12) e i flush per-test. I task orfani di run
    # morti restano su code suffissate senza consumer.
    isolate_task_queues(env)
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
    # Boot warm ~2s, ma l'import di app.main tira geopandas/gdal/pandas: sotto
    # saturazione (es. subito dopo la suite unit completa) l'import puo' non
    # produrre nemmeno il primo log entro 30s, facendo scattare il timeout come
    # ERROR ("still running / no output") invece di un boot reale fallito. 60s
    # da' margine senza mascherare un crash (l'exit-code del child resta
    # diagnosticato da wait_for_http_ready). Override via env per CI lente.
    _backend_boot_timeout = float(integration_env.get("INTEGRATION_BOOT_TIMEOUT_S", "60"))
    wait_for_http_ready(f"{url}/", timeout_s=_backend_boot_timeout, process=proc)
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
    # Stesso razionale del backend: import pesante sotto carico. 40s di margine.
    _fake_api_timeout = float(integration_env.get("INTEGRATION_BOOT_TIMEOUT_S", "60")) * 2 / 3
    wait_for_http_ready(f"{url}/health", timeout_s=_fake_api_timeout, process=proc)
    try:
        yield {"base_url": url, "process": proc}
    finally:
        stop_process(proc)


@pytest.fixture(scope="module")
def integration_task_worker(integration_env):
    """Worker Dramatiq reale contro RabbitMQ dei container di test.

    Isolamento: code suffissate per-modulo (isolate_task_queues in
    integration_env) — niente flush del broker; lo stato/result dei task
    e' su PostgreSQL, keyed per task_id."""
    proc = spawn_worker(
        workdir=INTEGRATION_ROOT.parent.parent,
        env=integration_env,
        loglevel="warning",
    )
    try:
        yield {"process": proc}
    finally:
        stop_process(proc)


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


# Auto-marker: requires_backend_http_service
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
        fixturenames = set(item.fixturenames)
        item_path = Path(str(item.fspath)).resolve()
        is_integration_file = item_path.is_relative_to(INTEGRATION_ROOT)
        filename = item_path.name

        if _HTTP_FIXTURES & fixturenames:
            item.add_marker(pytest.mark.requires_backend_http_service)

        if _BACKEND_STACK_FIXTURES & fixturenames:
            item.add_marker(pytest.mark.requires_postgres)
            item.add_marker(pytest.mark.requires_redis)

        if "integration_task_worker" in fixturenames:
            item.add_marker(pytest.mark.requires_celery)

        if not is_integration_file:
            continue

        # Tag every test under tests2/integration/ with the canonical
        # `integration` marker. The repo-wide autouse fixture
        # `_stub_pg_connection_execute` (in `tests2/conftest.py`) replaces
        # `app.db.pg_connection.execute` with a no-op for any test that
        # does NOT carry this marker, to shield unit tests from psycopg2
        # crashes. Without this line the integration suite silently runs
        # against the no-op stub, so cleanup DELETEs (group cascade,
        # api_connections, ...) report rowcount=0 even though the rows
        # exist — which then breaks the foreign-key teardown of the
        # group itself.
        item.add_marker(pytest.mark.integration)

        # Gate corsia integration: questi test richiedono servizi reali (DB
        # esterni, worker Dramatiq, backend HTTP live) forniti dallo stack
        # `backend/test-e2e-v2`. Nel run unit di default i servizi non ci sono e
        # i fixture-setup ERRORavano; ora SKIPpano in modo pulito. Per eseguirli
        # davvero: `URBY_RUN_INTEGRATION=1` con lo stack e2e-v2 attivo.
        if os.getenv("URBY_RUN_INTEGRATION") != "1":
            item.add_marker(pytest.mark.skip(
                reason="integration: richiede servizi reali (e2e-v2). "
                       "Esegui con URBY_RUN_INTEGRATION=1."
            ))

        if filename in _TIER1_INSTALL_VALIDATION_FILES:
            item.add_marker(pytest.mark.install_validation)
        else:
            item.add_marker(pytest.mark.developer_nightly)

        if filename in _DEVELOPER_NIGHTLY_FILES:
            item.add_marker(pytest.mark.developer_nightly)

        if filename in _POSTGRES_PROVIDER_FILES:
            item.add_marker(pytest.mark.requires_postgres)
            item.add_marker(pytest.mark.developer_nightly)

        if filename in _MIXED_OPTIONAL_PROVIDER_MARKERS:
            item.add_marker(pytest.mark.optional_provider)
            item.add_marker(pytest.mark.developer_nightly)
            for marker_name in _MIXED_OPTIONAL_PROVIDER_MARKERS[filename]:
                item.add_marker(getattr(pytest.mark, marker_name))

        for prefix, marker_names in _OPTIONAL_PROVIDER_PREFIX_MARKERS:
            if filename.startswith(prefix):
                item.add_marker(pytest.mark.optional_provider)
                item.add_marker(pytest.mark.developer_nightly)
                for marker_name in marker_names:
                    item.add_marker(getattr(pytest.mark, marker_name))
                break
