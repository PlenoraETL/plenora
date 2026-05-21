"""Typed payload contracts for admin system status probes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field


StatusValue: TypeAlias = Literal["ok", "warn", "error", "unknown"]
StatusScope: TypeAlias = Literal["local_replica", "cluster"]

LOCAL_REPLICA_SCOPE_WARNING = (
    "Dato locale alla replica backend che ha servito la richiesta; "
    "non rappresenta l'intero cluster."
)


class ScheduleFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class WorkerDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    hostname: str
    pid: int | None = None
    pool_size: int | None = None
    child_processes: int
    active_tasks: int
    total_tasks_processed: int | None = None
    queues: list[str]


class DiskStatus(BaseModel):
    """Stato del disco esposto in `GET /admin/system-status`.

    `path` e `usage_path` rivelano la struttura del filesystem server
    (es. `/var/plenora/data`, `/var`) e sono escludibili dalla risposta
    API via `Field(exclude=True)`. Restano disponibili internamente
    per logging/troubleshooting ma non finiscono nel payload pubblico.
    Pre-fix venivano serializzati in chiaro: info leak per admin
    compromesso che ne ricava la mappa del filesystem per persistenza.
    """

    model_config = ConfigDict(extra="forbid")

    path: str | None = Field(default=None, exclude=True)
    usage_path: str = Field(exclude=True)
    total_gb: float
    used_gb: float
    free_gb: float
    percent: float


class MemoryStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_gb: float
    available_gb: float
    used_gb: float
    percent: float


class CpuStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int
    load_1m: float | None
    load_5m: float | None
    load_15m: float | None
    percent: float


class DbSchemaTableStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None
    rows: int
    size_bytes: int
    size_pretty: str


class AdminStatusBlock(BaseModel):
    """One dashboard card payload.

    The optional fields are the stable public keys used by the existing
    dashboard cards. Serialization happens explicitly through ``to_api_dict``.
    """

    model_config = ConfigDict(extra="forbid")

    status: StatusValue
    message: str
    error: str | None = None
    scope: StatusScope | None = None
    scope_warning: str | None = None
    warnings: list[str] | None = None

    last_key: str | None = None
    last_modified: str | None = None
    size_bytes: int | None = None
    age_hours: float | None = None
    total_backups: int | None = None

    last_result: str | None = None
    last_run_at: str | None = None
    age_days: float | None = None
    exit_code: int | None = None

    total_active: int | None = None
    failing_count: int | None = None
    failing: list[ScheduleFailure] | None = None

    nodes: int | None = None
    concurrency_slots: int | None = None
    active_tasks: int | None = None
    queue_depth: int | None = None
    details: list[WorkerDetail] | None = None
    workers_online: int | None = None

    disk: DiskStatus | None = None
    memory: MemoryStatus | None = None
    cpu: CpuStatus | None = None

    max_connections: int | None = None
    total: int | None = None
    active: int | None = None
    idle: int | None = None
    idle_in_transaction: int | None = None
    waiting: int | None = None
    percent: float | None = None

    used_memory_bytes: int | None = None
    used_memory_human: str | None = None
    max_memory_bytes: int | None = None
    connected_clients: int | None = None
    uptime_seconds: int | None = None

    counters: dict[str, int] | None = None
    last_ok_epoch: float | None = None
    minutes_since_last_ok: float | None = None

    by_severity: dict[str, int] | None = None

    size_pretty: str | None = None
    size_gb: float | None = None
    tables: list[DbSchemaTableStatus] | None = None

    def __getitem__(self, key: str) -> object:
        return self.to_api_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_api_dict()

    def to_api_dict(self) -> Mapping[str, object]:
        data = self.model_dump(exclude_unset=True)
        scope = data.get("scope") or "cluster"
        data["scope"] = scope
        if scope == "local_replica":
            scope_warning = (
                data.get("scope_warning")
                or LOCAL_REPLICA_SCOPE_WARNING
            )
            data["scope_warning"] = scope_warning
            warnings = list(data.get("warnings") or [])
            if scope_warning not in warnings:
                warnings.insert(0, scope_warning)
            data["warnings"] = warnings
        return data


class SystemStatusPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    backup: AdminStatusBlock
    verify_restore: AdminStatusBlock
    schedules: AdminStatusBlock
    workers: AdminStatusBlock
    host_resources: AdminStatusBlock
    db_pool: AdminStatusBlock
    redis: AdminStatusBlock
    db_schema: AdminStatusBlock
    jwt_blacklist: AdminStatusBlock
    tickets: AdminStatusBlock

    def __getitem__(self, key: str) -> object:
        return self.to_api_dict()[key]

    def __contains__(self, key: object) -> bool:
        return key in self.to_api_dict()

    def to_api_dict(self) -> Mapping[str, object]:
        return {
            "generated_at": self.generated_at.isoformat(),
            "backup": self.backup.to_api_dict(),
            "verify_restore": self.verify_restore.to_api_dict(),
            "schedules": self.schedules.to_api_dict(),
            "workers": self.workers.to_api_dict(),
            "host_resources": self.host_resources.to_api_dict(),
            "db_pool": self.db_pool.to_api_dict(),
            "redis": self.redis.to_api_dict(),
            "db_schema": self.db_schema.to_api_dict(),
            "jwt_blacklist": self.jwt_blacklist.to_api_dict(),
            "tickets": self.tickets.to_api_dict(),
        }
