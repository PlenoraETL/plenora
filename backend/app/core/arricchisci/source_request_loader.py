"""Source loading use-case for enrichment validate/estimate requests."""

from __future__ import annotations

from typing import Any

from app.core.artifacts import LatestProjectArtifactRef, SourceLoader, SourceRef
from app.core.arricchisci.types import JsonObject
from app.core.projects.upstream_identity import source_identity_need
from app.shared.contracts.auth import CurrentUser
from app.shared.execution.runtime_identity import route_upstream_source


def load_request_data(
    request: Any,
    current_user: CurrentUser,
    *,
    source_loader_cls=SourceLoader,
    source_ref_cls=SourceRef,
    latest_project_artifact_ref_cls=LatestProjectArtifactRef,
) -> JsonObject | None:
    """Load source data for enrichment validation/estimation requests."""
    if request.file_source_id:
        ref = source_ref_cls(file_source_id=request.file_source_id)
    elif request.project_id:
        # La porta unica (runtime_identity U1): un progetto con «Utente che
        # esegue» (anche a monte) non si legge dal risultato salvato.
        route_upstream_source(
            source_project_id=str(request.project_id),
            need=source_identity_need(str(request.project_id), group_id=current_user.group_id),
            isolated_upstreams=None,
        )
        ref = source_ref_cls(
            project=latest_project_artifact_ref_cls(project_id=request.project_id)
        )
    else:
        return None
    return source_loader_cls().load(
        ref,
        expected_group_id=current_user.group_id,
        is_superadmin=current_user.role == "superadmin",
    )
