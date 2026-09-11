"""Typed request and result models of the native sidecar path.

No dataframe crosses this boundary. A run is described by file paths and
bounds, and answers with an artifact path plus the stage evidence that the
legacy pandas path cannot produce (row-scoped diagnostics, declared loss).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from app.core.native_pipeline.capabilities import (
    NativeCapability,
    PipelineOperation,
    SourceKind,
)
from app.core.native_pipeline.stages import PipelineStage


@dataclass(frozen=True, slots=True)
class TableReadSpec:
    """A bounded single-table read.

    ``dsn_env_var`` is the *name* of the environment variable holding the DSN.
    The value never reaches argv: the runner forwards it to the child
    environment for the database stage only.
    """

    dsn_env_var: str
    schema: str
    table: str
    max_rows: int
    max_output_bytes: int
    order_by: str | None = None


@dataclass(frozen=True, slots=True)
class NativePipelineRequest:
    """One read -> plan -> convert run ending in a single artifact."""

    source: TableReadSpec
    plan_path: Path
    output_path: Path
    source_kind: SourceKind = SourceKind.POSTGRES
    operation: PipelineOperation = PipelineOperation.READ


@dataclass(frozen=True, slots=True)
class StageReport:
    """Observed evidence of a single subprocess stage."""

    stage: PipelineStage
    argv: tuple[str, ...]
    exit_code: int
    duration_ms: int
    stdout_bytes: int
    stderr_bytes: int
    stderr_tail: str
    report: Mapping[str, Any] | None

    def to_json_mapping(self) -> Mapping[str, Any]:
        """Render the stage as a JSON-serialisable mapping."""
        return {
            "stage": self.stage.value,
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "stdout_bytes": self.stdout_bytes,
            "stderr_bytes": self.stderr_bytes,
            "stderr_tail": self.stderr_tail,
            "report": None if self.report is None else dict(self.report),
        }


@dataclass(frozen=True, slots=True)
class LossReport:
    """Loss declared by the IO conversion stage.

    ``declared`` false means the tool declared nothing, not that nothing was
    lost: the native path reports what the pinned release reports.
    """

    declared: bool
    entries: tuple[Mapping[str, Any], ...]

    def to_json_mapping(self) -> Mapping[str, Any]:
        return {
            "declared": self.declared,
            "entries": [dict(entry) for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class NativePipelineResult:
    """Outcome of a successful run: an artifact plus its provenance."""

    capability: NativeCapability
    artifact_path: Path
    artifact_bytes: int
    stages: tuple[StageReport, ...]
    loss: LossReport
    diagnostics: tuple[Mapping[str, Any], ...]
    toolchain: Mapping[str, str]

    def to_json_mapping(self) -> Mapping[str, Any]:
        """Render the whole result as a JSON-serialisable mapping."""
        return {
            "capability": self.capability.value,
            "artifact_path": str(self.artifact_path),
            "artifact_bytes": self.artifact_bytes,
            "stages": [stage.to_json_mapping() for stage in self.stages],
            "loss": self.loss.to_json_mapping(),
            "diagnostics": [dict(entry) for entry in self.diagnostics],
            "toolchain": dict(self.toolchain),
        }


__all__ = [
    "LossReport",
    "NativePipelineRequest",
    "NativePipelineResult",
    "StageReport",
    "TableReadSpec",
]
