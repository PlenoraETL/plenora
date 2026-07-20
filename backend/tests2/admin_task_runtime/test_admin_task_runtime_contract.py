"""Security and aggregation contracts for the task runtime dashboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.admin_task_runtime import router as router_module
from app.core.admin_task_runtime import service
from app.core.auth.dependencies import require_superadmin
from app.core.task_runtime.probes import RabbitmqQueueStats
from app.core.task_runtime.runtime_leases import RunningTaskClaim, WorkerHeartbeat
from app.core.task_runtime.uncertain_recovery import UncertainResolutionOutcome
from app.shared.contracts.auth import CurrentUser


def _client(*, authenticated: bool) -> TestClient:
    app = FastAPI()
    if authenticated:
        app.dependency_overrides[require_superadmin] = lambda: CurrentUser(
            user_id="root",
            username="root",
            group_id="root",
            role="superadmin",
        )
    app.include_router(router_module.router, prefix="/api")
    return TestClient(app, raise_server_exceptions=False)


def test_dashboard_requires_superadmin() -> None:
    response = _client(authenticated=False).get("/api/admin/task-runtime")
    assert response.status_code in {401, 403}


def test_dashboard_exposes_typed_payload_to_superadmin(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        router_module,
        "get_task_runtime_payload",
        lambda: {
            "generated_at": now,
            "status": "ok",
            "broker_available": True,
            "broker_message": "ok",
            "queues": [],
            "workers": [],
            "running_tasks": [],
            "totals": {
                "messages_ready": 0,
                "dead_letters": 0,
                "consumers": 0,
                "workers_online": 0,
                "workers_stale": 0,
                "running_tasks": 0,
                "expired_task_leases": 0,
            },
            "warnings": [],
        },
    )

    response = _client(authenticated=True).get("/api/admin/task-runtime")

    assert response.status_code == 200
    assert response.json()["totals"]["messages_ready"] == 0


def test_service_aggregates_queues_workers_and_expired_leases(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    active_names = service._active_queue_names()
    dead_names = tuple(f"{name}.XQ" for name in active_names)

    monkeypatch.setattr(
        service.runtime_ports,
        "inspect_rabbitmq_queues",
        lambda *_args, **_kwargs: [
            RabbitmqQueueStats(name, True, 2 if name == active_names[0] else 0, 1)
            for name in active_names
        ] + [RabbitmqQueueStats(name, True, 0, 0) for name in dead_names],
    )
    monkeypatch.setattr(
        service.runtime_leases,
        "list_worker_heartbeats",
        lambda **_kwargs: [
            WorkerHeartbeat(
                worker_id="worker-1",
                role="data-plane",
                queues=(active_names[0],),
                hostname="host-a",
                pid=123,
                started_at=now - timedelta(minutes=10),
                heartbeat_at=now,
            )
        ],
    )
    monkeypatch.setattr(
        service.uncertain_recovery,
        "list_uncertain_carica_runs",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        service.runtime_leases,
        "list_running_task_claims",
        lambda **_kwargs: [
            RunningTaskClaim(
                task_id="task-running",
                actor_name="pipeline.execute",
                worker_id="worker-1",
                acquired_at=now - timedelta(seconds=30),
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=30),
                progress=40,
            ),
            RunningTaskClaim(
                task_id="task-expired",
                actor_name="pipeline.execute",
                worker_id="worker-lost",
                acquired_at=now - timedelta(minutes=2),
                heartbeat_at=now - timedelta(minutes=1),
                lease_expires_at=now - timedelta(seconds=1),
            ),
        ],
    )

    payload = service.get_task_runtime_payload()

    assert payload.totals.messages_ready == 2
    assert payload.totals.consumers == len(active_names)
    assert payload.totals.workers_online == 1
    assert payload.totals.running_tasks == 1
    assert payload.totals.expired_task_leases == 1
    assert payload.status == "warn"
    assert payload.running_tasks[1].status == "lease_expired"


def test_uncertain_resolution_requires_superadmin() -> None:
    response = _client(authenticated=False).post(
        "/api/admin/task-runtime/uncertain-carica/run-1/resolve",
        json={"resolution": "allow_retry", "note": "Target verificato"},
    )
    assert response.status_code in {401, 403}


def test_superadmin_can_resolve_uncertain_run(monkeypatch) -> None:
    monkeypatch.setattr(
        router_module,
        "resolve_uncertain_carica_run",
        lambda **_kwargs: UncertainResolutionOutcome(
            run_id="run-1",
            previous_status="completion_uncertain",
            status="failed",
            resolution="allow_retry",
        ),
    )

    response = _client(authenticated=True).post(
        "/api/admin/task-runtime/uncertain-carica/run-1/resolve",
        json={
            "resolution": "allow_retry",
            "note": "Destinazione verificata, nessuna riga presente",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["resolution"] == "allow_retry"
