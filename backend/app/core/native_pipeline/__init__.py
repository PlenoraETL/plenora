"""Explicit, capability-gated native path over the pinned Rust releases.

The public surface is deliberately narrow: build a
:class:`NativePipelineRequest`, hand it to :func:`run_native_pipeline` with a
:class:`NativeToolchainConfig`, and receive an artifact path plus stage
evidence. No dataframe crosses this boundary, and the legacy pandas path is
never used as a fallback.

See ``README.md`` in this package for the supported capabilities and the
binary pinning contract.
"""

from __future__ import annotations

from app.core.native_pipeline.capabilities import (
    CapabilitySupport,
    NativeCapability,
    PipelineOperation,
    SourceKind,
    capability_matrix,
    resolve_capability,
)
from app.core.native_pipeline.config import (
    NativeToolchainConfig,
    PinnedBinary,
)
from app.core.native_pipeline.errors import (
    DataPlanRejected,
    InvalidNativeRequest,
    MalformedStageOutput,
    NativePipelineDisabled,
    NativePlatformUnsupported,
    NativePipelineError,
    OutputAlreadyExists,
    ProcessSpawnFailed,
    StageArtifactOverflow,
    StageFailed,
    StageStreamOverflow,
    StageTimeout,
    ToolchainIdentityError,
    UnsupportedCapability,
    WorkspaceCleanupFailed,
)
from app.core.native_pipeline.models import (
    LossReport,
    NativePipelineRequest,
    NativePipelineResult,
    StageReport,
    TableReadSpec,
)
from app.core.native_pipeline.pipeline import run_native_pipeline
from app.core.native_pipeline.stages import PipelineStage


__all__ = [
    "CapabilitySupport",
    "DataPlanRejected",
    "InvalidNativeRequest",
    "LossReport",
    "MalformedStageOutput",
    "NativeCapability",
    "NativePipelineDisabled",
    "NativePlatformUnsupported",
    "NativePipelineError",
    "NativePipelineRequest",
    "NativePipelineResult",
    "NativeToolchainConfig",
    "OutputAlreadyExists",
    "PinnedBinary",
    "PipelineOperation",
    "PipelineStage",
    "ProcessSpawnFailed",
    "SourceKind",
    "StageArtifactOverflow",
    "StageFailed",
    "StageReport",
    "StageStreamOverflow",
    "StageTimeout",
    "TableReadSpec",
    "ToolchainIdentityError",
    "UnsupportedCapability",
    "WorkspaceCleanupFailed",
    "capability_matrix",
    "resolve_capability",
    "run_native_pipeline",
]
