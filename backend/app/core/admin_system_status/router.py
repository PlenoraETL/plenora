"""Router admin stato sistema — GET /api/admin/system-status."""

from __future__ import annotations

from typing import Mapping

from fastapi import APIRouter, Depends

from app.core.admin_system_status import service
from app.core.auth.dependencies import require_superadmin
from app.shared.contracts.auth import CurrentUser


router = APIRouter(prefix="/admin/system-status", tags=["admin-system-status"])


@router.get("", summary="Aggregato stato sistema per dashboard admin")
def get_system_status(
    _user: CurrentUser = Depends(require_superadmin),
) -> Mapping[str, object]:
    """Ritorna un JSON con metriche aggregate per il dashboard admin:

    - `backup`: stato ultimo dump S3 (soglie 26h/48h)
    - `verify_restore`: stato ultimo `plenora-backup-test.service`
    - `schedules`: count attivi + failing negli ultimi 7gg
    - `workers`: Celery worker online + task attivi + queue depth
    - `host_resources`: disco/RAM/CPU via psutil (warn 80%, error 92-95%)
    - `db_pool`: conn pool Postgres (attive/idle/waiting, warn 80%)
    - `redis`: broker/cache memory + clients + uptime
    - `db_schema`: size schema `plenora` + row count tabelle critiche
    - `jwt_blacklist`: stato blacklist JWT (Redis) + contatori ops + warning "inoperante da X min"

    Ogni blocco ha `status` in {ok, warn, error, unknown} e `message`
    human-readable. Ogni blocco espone anche `scope`:
    `cluster` per dipendenze condivise e `local_replica` per metriche
    della sola replica backend che risponde. I blocchi locali includono
    `scope_warning`/`warnings`. Errori di uno NON bloccano gli altri
    (graceful degradation).
    """
    return service.get_system_status().to_api_dict()
