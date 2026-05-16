"""IMDF bundle path, cache hydration, and ownership use-cases."""

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


def hydrate_local_cache(bundle_task_id: str, *, root: Path, logger: Any) -> None:
    """Hydrate the local bundle cache from object storage when needed."""
    try:
        from app.config import settings as runtime_settings

        if runtime_settings.IMDF_BUNDLES_DIR.resolve() == root.resolve():
            bundle_store.ensure_local_bundle(bundle_task_id)
        else:
            bundle_store.ensure_local_bundle(bundle_task_id, root=root)
    except FileNotFoundError:
        pass
    except (OSError, RuntimeError, ValueError) as e:
        logger.warning(f"[{bundle_task_id}] hydrate cache fallita (non-fatal): {e}")


def assert_bundle_group_access(
    bundle_dir: Path,
    current_user: CurrentUser,
    *,
    logger: Any,
    visible_checker: Any = assert_resource_visible,
) -> None:
    """Verify that the current user can access the project that owns a bundle."""
    if current_user.role == "superadmin":
        return

    meta = bundle_store.read_metadata(bundle_dir)
    if meta is None:
        logger.warning(
            f"_assert_bundle_group_access: bundle '{bundle_dir.name}' senza "
            f"plenora_metadata.json - accesso NEGATO a utente "
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
            f"plenora_metadata.json illeggibile ({bundle_dir.name}): {e} - "
            f"accesso NEGATO (fail-closed)"
        )
        raise HTTPException(
            status_code=403,
            detail="Metadata bundle illeggibile: accesso non consentito",
        )

    if not bundle_project_id:
        logger.warning(
            f"plenora_metadata.json senza project_id ({bundle_dir.name}) - "
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
        logger.error(f"_assert_bundle_group_access: errore verifica: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Errore verifica accesso bundle",
        )
