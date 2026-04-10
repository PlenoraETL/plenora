"""
conftest.py per tests_v5/scheduler/integration/
Fixture e builder per i test di integrazione dello Scheduler.

Prerequisiti:
  - Backend FastAPI su localhost:8000 (Celery ALWAYS_EAGER)
  - Docker PostgreSQL su localhost:5432
"""

import time
import uuid
import pytest
from typing import Dict, Any, Optional, Callable

# =============================================================================
# COSTANTI
# =============================================================================

TASK_POLL_TIMEOUT  = 120
TASK_POLL_INTERVAL = 1.0


# =============================================================================
# HELPER: POLLING
# =============================================================================

def poll_task(client, task_id: str, timeout: int = TASK_POLL_TIMEOUT) -> Dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        res = client.get(f"/tasks/{task_id}")
        if res.status_code == 404:
            time.sleep(TASK_POLL_INTERVAL)
            continue
        assert res.status_code == 200, f"Errore polling: {res.text}"
        data = res.json()
        status = data.get("status", "")
        if status in ("success", "SUCCESS"):
            return data.get("result", data)
        if status in ("failure", "FAILURE", "revoked", "REVOKED"):
            raise AssertionError(f"Task {task_id} fallito: {data}")
        time.sleep(TASK_POLL_INTERVAL)
    raise TimeoutError(f"Task {task_id} non completato entro {timeout}s")


# =============================================================================
# BUILDER: SCHEDULE
# =============================================================================

def build_schedule_data(
    project_id: str,
    cron_expression: str = "* * * * *",  # ogni minuto per i test
    timezone: str = "UTC",
    description: str = "",
    use_cache: bool = False,
    max_retries: int = 0,
    append_conflicts: str = "skip",
    destination: Optional[Dict] = None,
) -> Dict:
    """Costruisce il payload per POST /api/schedules."""
    data = {
        "project_id":              project_id,
        "cron_expression":         cron_expression,
        "timezone":                timezone,
        "description":             description,
        "use_cache":               use_cache,
        "max_retries":             max_retries,
        "retry_backoff_seconds":   [60, 300, 900],
        "append_conflicts":        append_conflicts,
        "log_skipped_rows":        True,
        "concurrency_max_queue":   3,
    }
    if destination:
        data["destination"] = destination
    return data


# =============================================================================
# FIXTURE: make_schedule
# =============================================================================

@pytest.fixture
def make_schedule(client, make_project) -> Callable:
    """Factory per creare schedule e pulirli al teardown."""
    created_ids = []

    def _create(
        project_id: str = None,
        cron_expression: str = "0 6 * * 1",
        connection_id: str = None,
        **kwargs,
    ) -> str:
        # Se non viene passato un project_id, crea un progetto di test
        if not project_id:
            project_id = make_project(
                name=f"[AUTO] S test {uuid.uuid4().hex[:8]}",
                project_type="estrai",
                connection_id=connection_id or "pg_conn",
                canvas={"nodes": [], "edges": []},
            )

        data = build_schedule_data(
            project_id=project_id,
            cron_expression=cron_expression,
            **kwargs,
        )
        res = client.post("/schedules", json=data)
        assert res.status_code == 200, f"Errore creazione schedule: {res.text}"
        schedule_id = res.json()["id"]
        created_ids.append(schedule_id)
        return schedule_id

    yield _create

    for sid in created_ids:
        try:
            client.delete(f"/schedules/{sid}")
        except Exception as e:
            print(f"[WARN] Teardown schedule {sid}: {e}")


# =============================================================================
# HELPER: ESECUZIONE MANUALE SCHEDULE
# =============================================================================

def run_schedule(client, schedule_id: str, timeout: int = 120) -> Dict[str, Any]:
    """Lancia esecuzione manuale di uno schedule e aspetta il risultato."""
    res = client.post(f"/schedules/{schedule_id}/run")
    assert res.status_code == 200, f"Errore avvio schedule: {res.text}"
    body = res.json()

    # Se asincrono con task_id → poll
    if "task_id" in body:
        return poll_task(client, body["task_id"], timeout=timeout)

    # Se sincrono (ALWAYS_EAGER) → risultato diretto
    return body


# =============================================================================
# FIXTURE: pg_connection_id per CARICA
# =============================================================================

@pytest.fixture(scope="session")
def pg_conn_carica(client) -> str:
    """Connessione PostgreSQL per test CARICA scheduler."""
    from tests_v5.conftest import TEST_DB_CONFIG
    conn_data = {
        "name": "[AUTO] S pg_carica",
        "type": "postgres",
        **TEST_DB_CONFIG["postgres"],
    }
    res = client.post("/connections", json=conn_data)
    if res.status_code != 200:
        pytest.skip("PostgreSQL Docker non disponibile")
    conn_id = res.json()["id"]
    yield conn_id
    client.delete(f"/connections/{conn_id}")
