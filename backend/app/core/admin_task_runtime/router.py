"""Superadmin read-only task runtime dashboard endpoint."""

from fastapi import APIRouter, Depends

from app.core.admin_task_runtime.models import TaskRuntimePayload
from app.core.admin_task_runtime.service import get_task_runtime_payload
from app.core.auth.dependencies import require_superadmin
from app.shared.contracts.auth import CurrentUser


router = APIRouter(prefix="/admin/task-runtime", tags=["admin-task-runtime"])


@router.get("", response_model=TaskRuntimePayload)
def get_task_runtime(
    current_user: CurrentUser = Depends(require_superadmin),
) -> TaskRuntimePayload:
    return get_task_runtime_payload()


__all__ = ["router"]
