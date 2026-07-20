"""Superadmin task-runtime dashboard and uncertain-run recovery endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.core.admin_task_runtime.models import (
    TaskRuntimePayload,
    UncertainCaricaResolutionRequest,
    UncertainCaricaResolutionResponse,
)
from app.core.admin_task_runtime.service import get_task_runtime_payload
from app.core.auth.dependencies import require_superadmin
from app.core.task_runtime.uncertain_recovery import (
    UncertainRunConflict,
    UncertainRunNotFound,
    resolve_uncertain_carica_run,
)
from app.shared.contracts.auth import CurrentUser


router = APIRouter(prefix="/admin/task-runtime", tags=["admin-task-runtime"])


@router.get("", response_model=TaskRuntimePayload)
def get_task_runtime(
    current_user: CurrentUser = Depends(require_superadmin),
) -> TaskRuntimePayload:
    return get_task_runtime_payload()


@router.post(
    "/uncertain-carica/{run_id}/resolve",
    response_model=UncertainCaricaResolutionResponse,
)
def resolve_uncertain_carica(
    run_id: str,
    payload: UncertainCaricaResolutionRequest,
    current_user: CurrentUser = Depends(require_superadmin),
) -> UncertainCaricaResolutionResponse:
    """Resolve a CARICA uncertainty barrier after operator verification."""
    try:
        outcome = resolve_uncertain_carica_run(
            run_id=run_id,
            resolution=payload.resolution,
            operator_user_id=current_user.user_id,
            note=payload.note,
        )
    except UncertainRunNotFound as exc:
        raise HTTPException(status_code=404, detail="Run non trovata") from exc
    except UncertainRunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return UncertainCaricaResolutionResponse(
        run_id=outcome.run_id,
        previous_status=outcome.previous_status,
        status=outcome.status,
        resolution=outcome.resolution,
    )


__all__ = ["router"]
