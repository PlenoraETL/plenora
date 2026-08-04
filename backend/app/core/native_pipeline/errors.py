"""Typed failures of the native sidecar path.

Two invariants hold for every error in this module:

* the message and the details are already redacted by the raiser, so an
  exception can be logged or serialised without leaking a DSN;
* raising means nothing was published — the caller's output path is either
  untouched or was already occupied before the run started.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import ClassVar, Mapping, Sequence

from app.core.native_pipeline.capabilities import NativeCapability
from app.core.native_pipeline.stages import PipelineStage


class NativePipelineError(Exception):
    """Base class of every native sidecar failure."""

    code: ClassVar[str] = "native_pipeline_error"

    def __init__(
        self, message: str, *, details: Mapping[str, object] | None = None
    ) -> None:
        super().__init__(message)
        self._details: Mapping[str, object] = MappingProxyType(dict(details or {}))

    @property
    def details(self) -> Mapping[str, object]:
        """JSON-serialisable, redacted context for machine-readable output."""
        return self._details


class NativePipelineDisabled(NativePipelineError):
    """The native path was invoked while the explicit opt-in was off."""

    code: ClassVar[str] = "native_pipeline_disabled"


class NativePlatformUnsupported(NativePipelineError):
    """The native runner is unavailable without POSIX process groups."""

    code: ClassVar[str] = "native_platform_unsupported"


class WorkspaceCleanupFailed(NativePipelineError):
    """A successful run could not remove its private sensitive workspace."""

    code: ClassVar[str] = "workspace_cleanup_failed"


class InvalidNativeRequest(NativePipelineError):
    """The request cannot be executed as written; checked before any spawn."""

    code: ClassVar[str] = "invalid_request"


class UnsupportedCapability(NativePipelineError):
    """The requested provider/operation has no qualified public CLI."""

    code: ClassVar[str] = "unsupported_capability"

    def __init__(self, capability: NativeCapability, reason: str) -> None:
        super().__init__(
            f"capability {capability.value} is not supported by the pinned "
            f"native toolchain: {reason}",
            details={"capability": capability.value, "reason": reason},
        )
        self.capability = capability


class ToolchainIdentityError(NativePipelineError):
    """A pinned binary did not prove its identity and version."""

    code: ClassVar[str] = "toolchain_identity"


class OutputAlreadyExists(NativePipelineError):
    """The final artifact path is occupied; the run never clobbers it."""

    code: ClassVar[str] = "output_already_exists"


class ProcessSpawnFailed(NativePipelineError):
    """The operating system refused to start a pinned binary."""

    code: ClassVar[str] = "stage_spawn_failed"

    def __init__(self, stage: PipelineStage, message: str) -> None:
        super().__init__(message, details={"stage": stage.value})
        self.stage = stage


class StageTimeout(NativePipelineError):
    """A stage exceeded its wall-clock budget and was killed."""

    code: ClassVar[str] = "stage_timeout"

    def __init__(self, stage: PipelineStage, timeout_ms: int) -> None:
        super().__init__(
            f"stage {stage.value} exceeded its {timeout_ms} ms budget and was killed",
            details={"stage": stage.value, "timeout_ms": timeout_ms},
        )
        self.stage = stage
        self.timeout_ms = timeout_ms


class StageStreamOverflow(NativePipelineError):
    """A stage wrote more bytes than the configured stream bound allows."""

    code: ClassVar[str] = "stage_stream_overflow"

    def __init__(self, stage: PipelineStage, stream: str, limit_bytes: int) -> None:
        super().__init__(
            f"stage {stage.value} exceeded the {limit_bytes} byte bound on {stream}",
            details={
                "stage": stage.value,
                "stream": stream,
                "limit_bytes": limit_bytes,
            },
        )
        self.stage = stage
        self.stream = stream
        self.limit_bytes = limit_bytes


class StageArtifactOverflow(NativePipelineError):
    """A stage artifact exceeded its configured bound while the child ran."""

    code: ClassVar[str] = "stage_artifact_overflow"

    def __init__(self, stage: PipelineStage, limit_bytes: int) -> None:
        super().__init__(
            f"stage {stage.value} exceeded its {limit_bytes} byte artifact bound",
            details={"stage": stage.value, "limit_bytes": limit_bytes},
        )
        self.stage = stage
        self.limit_bytes = limit_bytes


class StageFailed(NativePipelineError):
    """A stage exited non-zero, or exited zero without its declared artifact."""

    code: ClassVar[str] = "stage_failed"

    def __init__(
        self,
        stage: PipelineStage,
        exit_code: int,
        reason: str,
        *,
        stderr_tail: str = "",
    ) -> None:
        super().__init__(
            f"stage {stage.value} failed with exit code {exit_code}: {reason}"
            + (f" | stderr: {stderr_tail}" if stderr_tail else ""),
            details={
                "stage": stage.value,
                "exit_code": exit_code,
                "reason": reason,
                "stderr_tail": stderr_tail,
            },
        )
        self.stage = stage
        self.exit_code = exit_code


class DataPlanRejected(NativePipelineError):
    """Data Tools refused the plan (exit 2); nothing is published.

    Exit 2 is a contractual rejection, not a crash: the diagnostics it carries
    are the row-scoped evidence the legacy pandas path cannot produce.
    """

    code: ClassVar[str] = "data_plan_rejected"

    def __init__(
        self,
        exit_code: int,
        diagnostics: Sequence[Mapping[str, object]],
        *,
        stderr_tail: str = "",
    ) -> None:
        frozen = tuple(diagnostics)
        super().__init__(
            f"the Data plan was rejected with exit code {exit_code} and "
            f"{len(frozen)} diagnostic entries"
            + (f" | stderr: {stderr_tail}" if stderr_tail else ""),
            details={
                "stage": PipelineStage.DATA_PLAN.value,
                "exit_code": exit_code,
                "diagnostics": [dict(entry) for entry in frozen],
                "stderr_tail": stderr_tail,
            },
        )
        self.stage = PipelineStage.DATA_PLAN
        self.exit_code = exit_code
        self.diagnostics = frozen


class MalformedStageOutput(NativePipelineError):
    """A stage emitted a report whose size or shape is not the pinned one."""

    code: ClassVar[str] = "malformed_stage_output"

    def __init__(self, stage: PipelineStage, reason: str) -> None:
        super().__init__(
            f"stage {stage.value} produced an unusable report: {reason}",
            details={"stage": stage.value, "reason": reason},
        )
        self.stage = stage


__all__ = [
    "DataPlanRejected",
    "InvalidNativeRequest",
    "MalformedStageOutput",
    "NativePipelineDisabled",
    "NativePlatformUnsupported",
    "NativePipelineError",
    "OutputAlreadyExists",
    "ProcessSpawnFailed",
    "StageArtifactOverflow",
    "StageFailed",
    "StageStreamOverflow",
    "StageTimeout",
    "ToolchainIdentityError",
    "UnsupportedCapability",
    "WorkspaceCleanupFailed",
]
