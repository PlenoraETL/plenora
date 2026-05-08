"""
N9 — helper condiviso per re-check permessi user al pickup di un task Celery.

Contesto: tra submit (endpoint HTTP) e pickup (worker Celery) puo' passare
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import HTTPException

from app.utils.logger import get_logger
from app.core.auth.db import get_user
from app.shared.contracts.auth import CurrentUser
from app.shared.authz_helpers import assert_project_access

logger = get_logger(__name__)


def recheck_user_authorized(
    user_id: Optional[str],
    project_id: str,
    *,
    task_id: str = "",
    run_id: str = "",
    task_name: str = "task",
) -> Optional[Dict[str, Any]]:
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
        if not user or not user.get("is_active"):
            logger.warning(
                f"[N9] {task_name} {task_id} rifiutato: user {user_id} "
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
            user_id=user["id"],
            username=user.get("username", ""),
            group_id=user.get("group_id", ""),
            role=user.get("role", ""),
        )
        try:
            assert_project_access(project_id, live_user)
        except HTTPException as http_exc:
            logger.warning(
                f"[N9] {task_name} {task_id} rifiutato: assert_project_access "
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
            f"[N9] {task_name} {task_id}: re-check user {user_id} fallito: {e}",
            run_id=run_id, project_id=project_id, exc_info=True,
        )
        return {
            "status": "permission_revoked",
            "success": False,
            "message": f"Re-check permessi fallito: {e}",
        }
