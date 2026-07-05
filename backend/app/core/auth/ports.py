"""
Porte di lettura risorse per i check di autorizzazione.

`auth.resource_access` non deve costruire servizi di altri domini ne'
toccare i loro moduli db: legge project metadata e file source attraverso
questi protocolli. Gli adapter concreti vivono nei domini proprietari
(`projects.access_context`, `files.ownership`); il binding avviene al
composition root (`app/runtime/services.py`) con fallback lazy identico
al comportamento dei contesti senza bootstrap esplicito.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol, runtime_checkable

from app.core.projects.models import ProjectMetadata

if TYPE_CHECKING:
    from app.core.files.db import FileSourcePublicRow


__all__ = [
    "FileSourceReader",
    "ProjectMetadataReader",
]


@runtime_checkable
class ProjectMetadataReader(Protocol):
    """Lettura metadata progetto per i check di accesso (404/403)."""

    def get_project_metadata(self, project_id: str) -> Optional[ProjectMetadata]:
        ...


@runtime_checkable
class FileSourceReader(Protocol):
    """Lettura file source per i check di visibilita' (404/403)."""

    def get_file_source(self, file_source_id: str) -> Optional["FileSourcePublicRow"]:
        ...
