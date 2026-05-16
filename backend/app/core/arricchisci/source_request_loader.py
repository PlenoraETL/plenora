"""Source loading use-case for enrichment validate/estimate requests."""

from __future__ import annotations

from typing import Any

from app.core.artifacts import LatestProjectArtifactRef, SourceLoader, SourceRef
from app.core.arricchisci.types import JsonObject
from app.shared.contracts.auth import CurrentUser


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
