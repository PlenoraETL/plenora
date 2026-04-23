"""Router admin backups — endpoint REST per la gestione backup PG -> S3.

Tutti gli endpoint richiedono ruolo superadmin.

Endpoint:
  - GET  /api/admin/backups          lista oggetti S3
  - GET  /api/admin/backups/status   stato ultimo run (da DB `plenora.backup_runs`)
  - GET  /api/admin/backups/schedule metadati schedule (env var, no systemctl)
  - POST /api/admin/backups/trigger  lancia backup manuale on-demand
"""

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth.auth import require_superadmin, CurrentUser
from app.core.admin_backups import service
from app.core.admin_backups.service import AdminBackupDependencyError

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])


@router.get("")
def list_backups_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> Dict[str, Any]:
    """Lista dei dump presenti nel bucket S3."""
    # Bug #4 (2026-04-23): dep runtime mancante (botocore[crt]) → 503
    # esplicito invece di lista vuota fuorviante.
    try:
        items = service.list_backups()
    except AdminBackupDependencyError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "aws_crt_missing",
                "message": (
                    "Dipendenza runtime AWS mancante (botocore[crt] / awscrt). "
                    "Installa la dipendenza sul server per abilitare questa pagina."
                ),
                "exception": str(e),
            },
        )
    return {
        "bucket": service.BUCKET,
        "region": service.REGION,
        "count": len(items),
        "items": items,
    }


@router.get("/status")
def backup_status_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> Dict[str, Any]:
    """Stato dell'ultimo run del service plenora-backup."""
    return service.get_last_run_status()


@router.get("/schedule")
def backup_schedule_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> Dict[str, Any]:
    """Info sul prossimo run schedulato del timer plenora-backup."""
    return service.get_schedule_info()


@router.post("/trigger")
def backup_trigger_endpoint(
    _user: CurrentUser = Depends(require_superadmin),
) -> Dict[str, Any]:
    """Lancia un backup manuale on-demand. Rifiuta se gia' in corso."""
    return service.trigger_manual_backup()
