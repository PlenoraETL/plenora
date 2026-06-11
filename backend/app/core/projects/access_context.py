"""Typed project access context helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.projects import db as project_db
from app.core.projects import service as project_service_module
from app.core.projects.types import OwnershipResource


@dataclass(frozen=True)
class ProjectAccessContext:
    project_id: str
    group_id: Optional[str]


@dataclass(frozen=True)
class ProjectOwnerContext:
    project_id: str
    group_id: Optional[str]
    created_by: Optional[str]

    def ownership_resource(self) -> OwnershipResource:
        return {
            "id": self.project_id,
            "group_id": self.group_id,
            "created_by": self.created_by,
        }


@dataclass(frozen=True)
class ProjectPublishContext:
    project_id: str
    group_id: Optional[str]
    project_type: str
    canvas: object


def load_project_access_context(project_id: str) -> Optional[ProjectAccessContext]:
    """Load the raw project tenant binding for access checks."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectAccessContext(project_id=row.id, group_id=row.group_id)


def load_project_owner_context(project_id: str) -> Optional[ProjectOwnerContext]:
    """Load project ownership fields for auth owner dependencies."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectOwnerContext(
        project_id=row.id,
        group_id=row.group_id,
        created_by=row.created_by,
    )


def load_project_visibility_resource(project_id: str) -> OwnershipResource | None:
    """Load the project resource shape consumed by shared visibility checks."""
    context = load_project_owner_context(project_id)
    if context is None:
        return None
    return context.ownership_resource()


def load_project_publish_context(project_id: str) -> Optional[ProjectPublishContext]:
    """Load the project fields required to publish an eseguibile."""
    row = project_db.get_project_row(project_id)
    if row is None:
        return None
    return ProjectPublishContext(
        project_id=row.id,
        group_id=row.group_id,
        project_type=row.project_type,
        canvas=row.canvas,
    )


def list_project_ids_visible_to_group(group_id: str) -> set[str]:
    """Return project IDs owned by a group for project-only visibility checks."""
    return project_db.list_project_ids_for_group(group_id)


class ProjectMetadataAccess:
    """Adapter concreto del port ``auth.ports.ProjectMetadataReader``.

    Vive nel dominio proprietario: ``auth.resource_access`` consuma il port
    senza costruire ``ProjectService``. La costruzione resta per-chiamata
    via attributo di modulo (stessa semantica del chiamante storico: i patch
    wholesale su ``app.core.projects.service.ProjectService`` continuano a
    intercettare).
    """

    def get_project_metadata(self, project_id: str):
        return project_service_module.ProjectService().get_project_metadata(project_id)
