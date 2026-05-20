"""Router admin runtime config - GET /api/admin/runtime-config."""

from __future__ import annotations

from typing import Mapping

from fastapi import APIRouter, Depends

from app.core.admin_runtime_config import service
from app.core.auth.dependencies import require_superadmin
from app.shared.contracts.auth import CurrentUser


router = APIRouter(prefix="/admin/runtime-config", tags=["admin-runtime-config"])


@router.get("", summary="Configurazione runtime read-only")
def get_runtime_config(
    _user: CurrentUser = Depends(require_superadmin),
) -> Mapping[str, object]:
    """Return redacted runtime configuration and service probes.

    This endpoint is intentionally read-only. Configuration editing and
    service restarts need a separate, audited operational boundary.
    """

    return service.get_runtime_config()

