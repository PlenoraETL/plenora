"""
Unified artifact + source loading model.

Contratto unico per artifact resolution e source loading.
"""

from app.core.artifacts.types import (
    ArtifactResolutionError,
    LatestProjectArtifactRef,
    MaterializedArtifact,
    PipelineArtifact,
    RunArtifactRef,
    RunBlob,
    SourceLoadingError,
    SourceRef,
)
from app.core.artifacts.resolver import ArtifactResolver
from app.core.artifacts.source_loader import SourceLoader

__all__ = [
    "ArtifactResolutionError",
    "ArtifactResolver",
    "LatestProjectArtifactRef",
    "MaterializedArtifact",
    "PipelineArtifact",
    "RunArtifactRef",
    "RunBlob",
    "SourceLoader",
    "SourceLoadingError",
    "SourceRef",
]
