"""Capability matrix of the pinned native toolchain.

The matrix describes what the *qualified releases* expose as a public CLI, not
what Plenora can do through its legacy pandas path. A capability marked
unsupported here is refused before any process is spawned: there is no
fallback, and no legacy re-routing.

Pinned releases behind this matrix: plenora-database v1.1.0,
plenora-data-tools v1.0.2, plenora-IO-tools v1.0.1.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping


class SourceKind(str, Enum):
    """Database family named by a pipeline request."""

    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQLSERVER = "sqlserver"
    ORACLE = "oracle"


class PipelineOperation(str, Enum):
    """Direction of the requested data movement."""

    READ = "read"
    WRITE = "write"


class NativeCapability(str, Enum):
    """Unit of capability gating."""

    POSTGRES_READ = "postgres.read"
    MYSQL_READ = "mysql.read"
    SQLSERVER_READ = "sqlserver.read"
    ORACLE_READ = "oracle.read"
    POSTGRES_WRITE = "postgres.write"
    MYSQL_WRITE = "mysql.write"
    SQLSERVER_WRITE = "sqlserver.write"
    ORACLE_WRITE = "oracle.write"
    DATA_PLAN_APPLY = "data.plan.apply"
    IO_CONVERT = "io.convert"


@dataclass(frozen=True, slots=True)
class CapabilitySupport:
    """Whether a capability is routable, and why."""

    supported: bool
    reason: str


_NO_PUBLIC_MYSQL_CLI = (
    "plenora-database v1.1.0 exposes no public MySQL export/read CLI; "
    "the qualified MySQL provider is reachable only from inside the crate"
)
_NO_PUBLIC_WRITE_CLI = (
    "no qualified release exposes a public write CLI; row-scoped affected_rows "
    "semantics cannot be proven from this side of the boundary"
)

_MATRIX: Mapping[NativeCapability, CapabilitySupport] = MappingProxyType(
    {
        NativeCapability.POSTGRES_READ: CapabilitySupport(
            supported=True,
            reason="plenora-database v1.1.0 exposes `postgres-read-ipc`",
        ),
        NativeCapability.DATA_PLAN_APPLY: CapabilitySupport(
            supported=True,
            reason="plenora-data-tools v1.0.2 exposes `run --plan`",
        ),
        NativeCapability.IO_CONVERT: CapabilitySupport(
            supported=True,
            reason="plenora-IO-tools v1.0.1 exposes `convert`",
        ),
        NativeCapability.MYSQL_READ: CapabilitySupport(
            supported=False, reason=_NO_PUBLIC_MYSQL_CLI
        ),
        NativeCapability.SQLSERVER_READ: CapabilitySupport(
            supported=False,
            reason="plenora-database v1.1.0 exposes no public SQL Server read CLI",
        ),
        NativeCapability.ORACLE_READ: CapabilitySupport(
            supported=False,
            reason="no qualified release ships an Oracle provider",
        ),
        NativeCapability.POSTGRES_WRITE: CapabilitySupport(
            supported=False, reason=_NO_PUBLIC_WRITE_CLI
        ),
        NativeCapability.MYSQL_WRITE: CapabilitySupport(
            supported=False, reason=_NO_PUBLIC_WRITE_CLI
        ),
        NativeCapability.SQLSERVER_WRITE: CapabilitySupport(
            supported=False, reason=_NO_PUBLIC_WRITE_CLI
        ),
        NativeCapability.ORACLE_WRITE: CapabilitySupport(
            supported=False, reason=_NO_PUBLIC_WRITE_CLI
        ),
    }
)

_ROUTING: Mapping[tuple[SourceKind, PipelineOperation], NativeCapability] = (
    MappingProxyType(
        {
            (SourceKind.POSTGRES, PipelineOperation.READ): (
                NativeCapability.POSTGRES_READ
            ),
            (SourceKind.MYSQL, PipelineOperation.READ): NativeCapability.MYSQL_READ,
            (SourceKind.SQLSERVER, PipelineOperation.READ): (
                NativeCapability.SQLSERVER_READ
            ),
            (SourceKind.ORACLE, PipelineOperation.READ): NativeCapability.ORACLE_READ,
            (SourceKind.POSTGRES, PipelineOperation.WRITE): (
                NativeCapability.POSTGRES_WRITE
            ),
            (SourceKind.MYSQL, PipelineOperation.WRITE): NativeCapability.MYSQL_WRITE,
            (SourceKind.SQLSERVER, PipelineOperation.WRITE): (
                NativeCapability.SQLSERVER_WRITE
            ),
            (SourceKind.ORACLE, PipelineOperation.WRITE): (
                NativeCapability.ORACLE_WRITE
            ),
        }
    )
)


def capability_matrix() -> Mapping[NativeCapability, CapabilitySupport]:
    """Return the immutable support matrix of the pinned releases."""
    return _MATRIX


def resolve_capability(
    source_kind: SourceKind, operation: PipelineOperation
) -> NativeCapability:
    """Map a (source, operation) pair to the capability it needs.

    The routing table is total over both enums: an unmapped pair is a
    programming error, not a runtime condition to recover from.
    """
    return _ROUTING[(source_kind, operation)]


__all__ = [
    "CapabilitySupport",
    "NativeCapability",
    "PipelineOperation",
    "SourceKind",
    "capability_matrix",
    "resolve_capability",
]
