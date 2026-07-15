"""
Router admin backups — endpoint REST per la gestione backup PG -> S3.

Tutti gli endpoint richiedono ruolo superadmin.
"""

from typing import Annotated
from typing_extensions import TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth.dependencies import require_superadmin
from app.shared.contracts.auth import CurrentUser
from app.core.admin_backups import errors as admin_backup_errors
from app.core.admin_backups import service
from app.core.admin_backups.service import (
    AdminBackupCredentialsError,
    AdminBackupDependencyError,
    AdminBackupDisabledError,
)
from app.observability.audit_action import audit_action

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])


class AdminBackupsListResponse(TypedDict):
    bucket: str
    region: str
    count: int
    items: list[service.BackupObjectPayload]
    next_cursor: str | None


@router.get("")
def list_backups_endpoint(
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    cursor: Annotated[str | None, Query(max_length=2048)] = None,
    _user: CurrentUser = Depends(require_superadmin),
) -> AdminBackupsListResponse:
    """Lista dei dump presenti nel bucket S3."""
    try:
        items, next_cursor = service.list_backups_page(
            limit=limit, cursor=cursor,
        )
    except AdminBackupDisabledError as e:
        # capability object storage off -> feature non
        # disponibile. NON e' lo stesso di "credenziali scadute"
        raise HTTPException(
            status_code=503,
            detail={
                "error": admin_backup_errors.OBJECT_STORAGE_DISABLED,
                "message": str(e),
            },
        )
    except AdminBackupDependencyError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": admin_backup_errors.AWS_CRT_MISSING,
                "message": (
                    "Dipendenza runtime AWS mancante (botocore[crt] / awscrt). "
                    "Installa la dipendenza sul server per abilitare questa pagina."
                ),
                "exception": str(e),
            },
        )
    except AdminBackupCredentialsError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": admin_backup_errors.AWS_CREDENTIALS_EXPIRED,
                "message": str(e),
            },
        )
    return {
        "bucket": service.BUCKET,
        "region": service.REGION,
        "count": len(items),
        "items": items,
        "next_cursor": next_cursor,
    }


@router.get("/status")
def backup_status_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> service.LastBackupRunStatus:
    """Stato dell'ultimo run del service plenora-backup."""
    return service.get_last_run_status()


@router.get("/schedule")
def backup_schedule_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> service.BackupScheduleInfo:
    """Info sul prossimo run schedulato del timer plenora-backup."""
    return service.get_schedule_info()


@router.post("/trigger")
@audit_action(action="admin.backup.trigger")
def backup_trigger_endpoint(
    # Rinominato `_user` -> `current_user` solo qui: l'extractor di
    # `audit_action` legge il `user_id` dal kwarg `current_user` (vedi
    # audit_action.py:147). Senza il rename, l'audit row registrava
    # `user_id=NULL` proprio sul trigger super-admin, dove la forensics
    # "chi ha lanciato il backup" e' la sola informazione interessante
    # RBAC invariato: la dependency e' sempre `require_superadmin`
    current_user: CurrentUser = Depends(require_superadmin),
) -> service.ManualBackupTriggerResult:
    """Lancia un backup manuale on-demand. Rifiuta se gia' in corso."""
    return service.trigger_manual_backup()
