"""Sub-service di lettura metadata progetto.

Il lato lettura metadata vive qui come interfaccia stretta. I nuovi chiamanti
dipendono da ``ProjectMetadataService`` (o dal port
``auth.ports.ProjectMetadataReader``, che questa classe soddisfa
strutturalmente); la facade delega qui e resta compatibile.

Gli hook monkeypatchati dai test sulla classe facade
(``_ensure_default_group``, i converter ``project_*row_to_metadata``)
restano di proprieta' della facade: il sub-service li risolve attraverso
``owner`` a call-time, cosi' i patch di classe continuano a valere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.core.projects import db as project_db
from app.core.projects.models import ProjectList, ProjectMetadata

if TYPE_CHECKING:
    from app.core.projects.service import ProjectService


class ProjectMetadataService:
    """Letture metadata progetto: lista, dettaglio, esistenza."""

    def __init__(self, owner: "ProjectService") -> None:
        self._owner = owner

    def list_projects(self, group_id: Optional[str] = None) -> ProjectList:
        """Lista progetti.

        - group_id=None → no filter (usato dal superadmin per vedere tutti i gruppi)
        - group_id="X" → solo progetti del gruppo X
        """
        self._owner._ensure_default_group()
        rows = project_db.list_projects(group_id)
        return ProjectList(
            projects=[
                self._owner.project_row_to_metadata(row) for row in rows
            ]
        )

    def get_project_metadata(
        self, project_id: str, group_id: Optional[str] = None
    ) -> Optional[ProjectMetadata]:
        """Carica metadati progetto.

        - group_id=None → no filter (qualsiasi gruppo); il check di accesso
          spetta al chiamante (es. assert_group_access nel router).
        - group_id="X" → solo se appartiene al gruppo X.
        """
        row = project_db.get_project_row(project_id, group_id=group_id)
        return self._owner.project_detail_row_to_metadata(row) if row else None

    def project_exists(self, project_id: str) -> bool:
        """Verifica se il progetto esiste."""
        return project_db.get_project_row(project_id) is not None


__all__ = ["ProjectMetadataService"]
