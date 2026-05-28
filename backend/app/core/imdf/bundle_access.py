"""IMDF bundle path and storage-meta ownership use-cases."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.core.auth.ownership import assert_resource_visible
from app.core.imdf import bundle_store
from app.core.imdf import project_access
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser


def safe_bundle_dir(bundle_task_id: str, *, root: Path) -> Path:
    """Resolve a bundle directory and reject path traversal."""
    try:
        return bundle_store.resolve_bundle_dir(bundle_task_id, root=root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="bundle_task_id non valido (ammessi solo [A-Za-z0-9_-])",
        )


# Tombstone (QUERY-1 step 3): `hydrate_local_cache` rimosso.
# Wrapper su `bundle_store.ensure_local_bundle` (a sua volta rimosso).
# Zero caller post IMDF-4 step 4 (ACL check via meta on storage).


def assert_bundle_group_access_from_meta(
    meta: Any,
    current_user: CurrentUser,
    bundle_task_id: str,
    *,
    logger: Any,
    visible_checker: Any = None,
) -> None:
    """Check di group access da meta storage IMDF.

    Accetta direttamente il dict del meta gia' caricato dal caller
    (tipicamente via ``imdf.storage.get_meta_only(task_id)``). Permette
    agli endpoint di servire le response da storage senza idratare file
    locali per il solo check di ownership.

    Same fail-closed policy:
      - superadmin -> bypass
      - meta None / non-dict -> 403
      - meta senza project_id -> 403
      - project_id presente ma progetto non in DB -> 404
      - project_id visible_checker fail -> 403/404 da visible_checker
    """
    if visible_checker is None:
        visible_checker = assert_resource_visible

    if current_user.role == "superadmin":
        return

    if meta is None:
        logger.warning(
            f"assert_bundle_group_access_from_meta: bundle '{bundle_task_id}' "
            f"senza plenora_metadata.json - accesso NEGATO a utente "
            f"'{current_user.username}' (fail-closed)"
        )
        raise HTTPException(
            status_code=403,
            detail="Bundle senza metadata di ownership: accesso non consentito",
        )

    try:
        bundle_project_id = meta.get("project_id")
    except (AttributeError, TypeError) as e:
        logger.warning(
            f"plenora_metadata illeggibile ({bundle_task_id}): {e} - "
            f"accesso NEGATO (fail-closed)"
        )
        raise HTTPException(
            status_code=403,
            detail="Metadata bundle illeggibile: accesso non consentito",
        )

    if not bundle_project_id:
        logger.warning(
            f"plenora_metadata senza project_id ({bundle_task_id}) - "
            f"accesso NEGATO (fail-closed)"
        )
        raise HTTPException(
            status_code=403,
            detail="Bundle senza project_id: accesso non consentito",
        )

    try:
        project = project_access.get_project_for_bundle(bundle_project_id)
        if not project:
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{bundle_project_id}' del bundle non trovato",
                },
            )
        visible_checker(project, "projects", current_user, "Progetto")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"assert_bundle_group_access_from_meta: errore verifica: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Errore verifica accesso bundle",
        )
