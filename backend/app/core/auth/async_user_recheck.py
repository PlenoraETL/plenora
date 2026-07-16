"""
Helper condiviso per re-check permessi user al pickup di un task Celery.

Contesto: tra submit (endpoint HTTP) e pickup (worker Celery) puo' passare
"""

from __future__ import annotations

from typing import Optional
from typing_extensions import TypedDict

from fastapi import HTTPException

from app.utils.logger import get_logger
from app.core.auth.db import get_user
from app.shared.contracts.auth import CurrentUser
from app.core.auth.resource_access import assert_project_access
from app.core.auth.ownership import assert_resource_visible

logger = get_logger(__name__)


class PermissionRevokedResult(TypedDict):
    status: str
    success: bool
    message: str


def recheck_user_authorized(
    user_id: Optional[str],
    project_id: str,
    *,
    task_id: str = "",
    run_id: str = "",
    task_name: str = "task",
) -> Optional[PermissionRevokedResult]:
    """Re-verifica permessi user al pickup del worker.

    Returns:
        None se autorizzato (task procede).
        dict con status='permission_revoked' + success=False + message se
        non autorizzato. Il chiamante puo' arricchirlo con run_id/task_id
        specifici e ritornarlo direttamente dal task body.

    Never raises — fail-closed: qualsiasi eccezione durante il re-check
    diventa permission_revoked (meglio rifiutare che procedere con
    permessi indeterminati).
    """
    if not user_id:
        return None

    try:
        user = get_user(user_id)
        if not user or not user.is_active:
            logger.warning(
                f"{task_name} {task_id} rifiutato: user {user_id} "
                f"non trovato o disabilitato",
                run_id=run_id, project_id=project_id,
            )
            return {
                "status": "permission_revoked",
                "success": False,
                "message": "Utente non autorizzato (disabilitato o rimosso)",
            }

        # Snapshot live: costruiamo CurrentUser dallo stato del DB ORA
        # non dallo stato al submit. assert_project_access alza HTTP 403
        # cross-group, HTTP 404 inesistente (gestito come revoked anche
        # quello — un project sparito durante la coda e' comunque no-go
        live_user = CurrentUser(
            user_id=user.id,
            username=user.username,
            group_id=user.group_id,
            role=user.role,
        )
        try:
            assert_project_access(project_id, live_user)
        except HTTPException as http_exc:
            logger.warning(
                f"{task_name} {task_id} rifiutato: assert_project_access "
                f"failed per user {user_id} / project {project_id}: "
                f"{http_exc.status_code} {http_exc.detail}",
                run_id=run_id, project_id=project_id,
            )
            return {
                "status": "permission_revoked",
                "success": False,
                "message": f"Accesso al progetto non piu' valido: {http_exc.detail}",
            }
        return None

    except Exception as e:
        logger.error(
            f"{task_name} {task_id}: re-check user {user_id} fallito: {e}",
            run_id=run_id, project_id=project_id, exc_info=True,
        )
        return {
            "status": "permission_revoked",
            "success": False,
            "message": f"Re-check permessi fallito: {e}",
        }


def recheck_eseguibile_authorized(
    user_id: Optional[str],
    eseguibile_id: str,
    *,
    task_id: str = "",
    run_id: str = "",
) -> Optional[PermissionRevokedResult]:
    """Re-check live equivalente, ma sul resource kind ``eseguibili``."""

    if not user_id:
        return None
    try:
        user = get_user(user_id)
        if not user or not user.is_active:
            return {
                "status": "permission_revoked",
                "success": False,
                "message": "Utente non autorizzato (disabilitato o rimosso)",
            }
        from app.core.eseguibili import db as eseguibili_db

        record = eseguibili_db.get_eseguibile(eseguibile_id)
        if record is None:
            return {
                "status": "permission_revoked",
                "success": False,
                "message": "Eseguibile non più disponibile",
            }
        live_user = CurrentUser(
            user_id=user.id,
            username=user.username,
            group_id=user.group_id,
            role=user.role,
        )
        try:
            assert_resource_visible(
                record, "eseguibili", live_user, "Eseguibile"
            )
        except HTTPException as exc:
            return {
                "status": "permission_revoked",
                "success": False,
                "message": f"Accesso all'eseguibile non più valido: {exc.detail}",
            }
        return None
    except Exception as exc:
        logger.error(
            f"Task eseguibile {task_id}: re-check user {user_id} fallito: {exc}",
            run_id=run_id,
            exc_info=True,
        )
        return {
            "status": "permission_revoked",
            "success": False,
            "message": "Re-check permessi eseguibile fallito",
        }
