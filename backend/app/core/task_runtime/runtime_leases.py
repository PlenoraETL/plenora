"""PostgreSQL worker heartbeats and task execution leases.

The registry is deployment-neutral: Windows processes, Linux services and
container workers all publish the same leases and are observed through the
same queries.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Sequence

from app.db.pg_connection import unit_of_work
from app.shared.error_codes import WORKER_LOST


WorkerRole = Literal["data-plane", "control-plane"]
TaskClaimOutcome = Literal["acquired", "completed", "busy"]


@dataclass(frozen=True)
class WorkerRoleCount:
    role: WorkerRole
    count: int


@dataclass(frozen=True)
class StaleTaskClaim:
    task_id: str
    actor_name: str
    worker_id: str


def register_worker(
    *,
    worker_id: str,
    role: WorkerRole,
    queues: Sequence[str],
    hostname: str,
    pid: int,
) -> None:
    queues_json = json.dumps(sorted(set(queues)), separators=(",", ":"))
    with unit_of_work() as session:
        session.execute(
            "INSERT INTO worker_heartbeats "
            "(worker_id, role, queues, hostname, pid, started_at, heartbeat_at) "
            "VALUES (%s, %s, %s::jsonb, %s, %s, now(), now()) "
            "ON CONFLICT (worker_id) DO UPDATE SET "
            "role = EXCLUDED.role, queues = EXCLUDED.queues, "
            "hostname = EXCLUDED.hostname, pid = EXCLUDED.pid, "
            "heartbeat_at = now()",
            (worker_id, role, queues_json, hostname, int(pid)),
        )


def heartbeat_worker(worker_id: str) -> None:
    with unit_of_work() as session:
        session.execute(
            "UPDATE worker_heartbeats SET heartbeat_at = now() "
            "WHERE worker_id = %s",
            (worker_id,),
        )


def unregister_worker(worker_id: str) -> None:
    with unit_of_work() as session:
        session.execute(
            "DELETE FROM worker_heartbeats WHERE worker_id = %s",
            (worker_id,),
        )


def count_fresh_workers(*, max_age_seconds: int) -> list[WorkerRoleCount]:
    with unit_of_work() as session:
        rows = session.fetchall(
            "SELECT role, count(*) AS count FROM worker_heartbeats "
            "WHERE heartbeat_at >= now() - (%s * interval '1 second') "
            "GROUP BY role ORDER BY role",
            (max(1, int(max_age_seconds)),),
        )
    return [
        WorkerRoleCount(role=str(row["role"]), count=int(row["count"]))
        for row in rows
    ]


def claim_task(
    *,
    task_id: str,
    actor_name: str,
    worker_id: str,
    lease_seconds: int,
) -> TaskClaimOutcome:
    """Atomically acquire a task execution claim.

    Completed task IDs are permanently deduplicated until retention cleanup.
    Failed or expired claims may be acquired by a broker retry.
    """
    lease_seconds = max(1, int(lease_seconds))
    with unit_of_work() as session:
        inserted = session.fetchone(
            "INSERT INTO task_execution_claims "
            "(task_id, actor_name, worker_id, state, lease_expires_at) "
            "VALUES (%s, %s, %s, 'running', "
            "now() + (%s * interval '1 second')) "
            "ON CONFLICT (task_id) DO NOTHING RETURNING task_id",
            (task_id, actor_name, worker_id, lease_seconds),
        )
        if inserted:
            return "acquired"

        acquired = session.fetchone(
            "UPDATE task_execution_claims SET actor_name = %s, worker_id = %s, "
            "state = 'running', acquired_at = now(), heartbeat_at = now(), "
            "lease_expires_at = now() + (%s * interval '1 second'), "
            "finished_at = NULL, updated_at = now() "
            "WHERE task_id = %s AND state <> 'completed' "
            "AND (state = 'failed' OR lease_expires_at <= now()) "
            "RETURNING task_id",
            (actor_name, worker_id, lease_seconds, task_id),
        )
        if acquired:
            return "acquired"

        row = session.fetchone(
            "SELECT state FROM task_execution_claims WHERE task_id = %s",
            (task_id,),
        )
    if row and str(row["state"]) == "completed":
        return "completed"
    return "busy"


def heartbeat_task_claims(
    *,
    worker_id: str,
    task_ids: Sequence[str],
    lease_seconds: int,
) -> None:
    if not task_ids:
        return
    with unit_of_work() as session:
        session.execute(
            "UPDATE task_execution_claims SET heartbeat_at = now(), "
            "lease_expires_at = now() + (%s * interval '1 second'), "
            "updated_at = now() WHERE worker_id = %s AND state = 'running' "
            "AND task_id = ANY(%s)",
            (max(1, int(lease_seconds)), worker_id, list(task_ids)),
        )


def finish_task_claim(
    *,
    task_id: str,
    worker_id: str,
    succeeded: bool,
) -> None:
    state = "completed" if succeeded else "failed"
    with unit_of_work() as session:
        session.execute(
            "UPDATE task_execution_claims SET state = %s, finished_at = now(), "
            "updated_at = now() WHERE task_id = %s AND worker_id = %s",
            (state, task_id, worker_id),
        )


def recover_stale_task_claims(
    *,
    limit: int,
    result_ttl_seconds: int,
    recovery_grace_seconds: int = 30,
) -> list[StaleTaskClaim]:
    """Fail expired running claims and expose a terminal task result."""
    bounded_limit = max(1, min(int(limit), 1000))
    ttl_seconds = max(1, int(result_ttl_seconds))
    grace_seconds = max(0, int(recovery_grace_seconds))
    payload = json.dumps(
        {
            "success": False,
            "error": WORKER_LOST,
            "message": "Worker non piu' raggiungibile durante il task",
        },
        separators=(",", ":"),
    )
    with unit_of_work() as session:
        rows = session.fetchall(
            "WITH stale AS ("
            " SELECT task_id FROM task_execution_claims"
            " WHERE state = 'running' AND lease_expires_at <= "
            "now() - (%s * interval '1 second')"
            " ORDER BY lease_expires_at FOR UPDATE SKIP LOCKED LIMIT %s"
            ") UPDATE task_execution_claims AS c SET state = 'failed',"
            " finished_at = now(), updated_at = now() FROM stale"
            " WHERE c.task_id = stale.task_id"
            " RETURNING c.task_id, c.actor_name, c.worker_id",
            (grace_seconds, bounded_limit),
        )
        for row in rows:
            session.execute(
                "INSERT INTO task_results "
                "(task_id, state, name, payload, expires_at) "
                "VALUES (%s, 'FAILURE', %s, %s::jsonb, "
                "now() + (%s * interval '1 second')) "
                "ON CONFLICT (task_id) DO UPDATE SET state = 'FAILURE', "
                "name = COALESCE(task_results.name, EXCLUDED.name), "
                "payload = EXCLUDED.payload, updated_at = now(), "
                "expires_at = EXCLUDED.expires_at "
                "WHERE task_results.state NOT IN ('SUCCESS', 'REVOKED')",
                (row["task_id"], row["actor_name"], payload, ttl_seconds),
            )
    return [
        StaleTaskClaim(
            task_id=str(row["task_id"]),
            actor_name=str(row["actor_name"]),
            worker_id=str(row["worker_id"]),
        )
        for row in rows
    ]


def cleanup_runtime_leases(*, retention_seconds: int) -> tuple[int, int]:
    retention = max(1, int(retention_seconds))
    with unit_of_work() as session:
        worker_cursor = session.execute(
            "DELETE FROM worker_heartbeats "
            "WHERE heartbeat_at < now() - (%s * interval '1 second')",
            (retention,),
        )
        claim_cursor = session.execute(
            "DELETE FROM task_execution_claims WHERE state <> 'running' "
            "AND finished_at < now() - (%s * interval '1 second')",
            (retention,),
        )
    return (
        int(getattr(worker_cursor, "rowcount", 0) or 0),
        int(getattr(claim_cursor, "rowcount", 0) or 0),
    )


__all__ = [
    "StaleTaskClaim",
    "TaskClaimOutcome",
    "WorkerRole",
    "WorkerRoleCount",
    "claim_task",
    "cleanup_runtime_leases",
    "count_fresh_workers",
    "finish_task_claim",
    "heartbeat_task_claims",
    "heartbeat_worker",
    "recover_stale_task_claims",
    "register_worker",
    "unregister_worker",
]
