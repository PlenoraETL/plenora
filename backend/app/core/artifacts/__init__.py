"""Unified artifact + source loading model.

Contratto unico per artifact resolution e source loading.

**Artifact model**
- `RunArtifactRef`: riferimento a un output specifico di una run
- `LatestProjectArtifactRef`: ultimo output noto di un progetto
- `PipelineArtifact`: lente strutturata sopra il dict di execute_pipeline
- `MaterializedArtifact`: blob materializzato su disco / inline
- `RunBlob`: blob raw + metadata per download HTTP
- `ArtifactResolver`: risolve i Ref in MaterializedArtifact con
  cross-tenant fail-closed

**Source loading**
- `SourceRef`: riferimento esattamente-una-sorgente (project / file /
  static), con factory `from_node_data` per i call site canvas-driven
- `SourceLoader`: dispatch unico file/project/static, fail-closed sul
  tenant binding, visibility check enforced dal loader stesso (non dal
  caller).

Tutti i componenti sono core-level: nessuna dipendenza da CurrentUser,
da FastAPI o dal layer HTTP. Eccezioni del modulo (`ArtifactResolutionError`,
`SourceLoadingError`) per evitare di importare HTTPException nel core;
i router HTTP catturano e traducono.
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
