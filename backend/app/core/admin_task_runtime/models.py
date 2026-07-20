"""Typed API contracts for the task runtime dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueueStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: Literal["work", "dead_letter"]
    exists: bool
    messages_ready: int
    consumers: int
    status: Literal["ok", "warn", "error"]


class WorkerStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str
    role: str
    queues: list[str]
    hostname: str
    pid: int
    started_at: datetime
    heartbeat_at: datetime
    heartbeat_age_seconds: int
    status: Literal["online", "stale"]


class RunningTaskStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    actor_name: str
    worker_id: str
    acquired_at: datetime
    heartbeat_at: datetime
    lease_expires_at: datetime
    elapsed_seconds: int
    lease_remaining_seconds: int
    status: Literal["running", "lease_expired"]
    progress: int | None = None
    message: str | None = None


class UncertainCaricaRunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    project_id: str
    group_id: str
    timestamp: datetime
    error_message: str | None = None


class UncertainCaricaResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["confirm_completed", "allow_retry"]
    note: str = Field(min_length=3, max_length=1000)


class UncertainCaricaResolutionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    previous_status: str
    status: Literal["completed", "failed"]
    resolution: Literal["confirm_completed", "allow_retry"]


class TaskRuntimeTotals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages_ready: int
    dead_letters: int
    consumers: int
    workers_online: int
    workers_stale: int
    running_tasks: int
    expired_task_leases: int
    uncertain_carica_runs: int = 0


class TaskRuntimePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    status: Literal["ok", "warn", "error"]
    broker_available: bool
    broker_message: str
    queues: list[QueueStatus]
    workers: list[WorkerStatus]
    running_tasks: list[RunningTaskStatus]
    uncertain_runs: list[UncertainCaricaRunStatus] = Field(default_factory=list)
    totals: TaskRuntimeTotals
    warnings: list[str]
