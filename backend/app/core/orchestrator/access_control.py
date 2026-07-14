"""Access-control use-cases for orchestrator HTTP handlers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from app.core.auth.ownership import assert_group_access
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import output_store as eseguibili_output_store
from app.core.orchestrator.router_mappers import task_result_project_id
from app.core.projects.service import ProjectService
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser


def _get_project_service() -> ProjectService:
    """Delegato all'accessor del router orchestrator (stack iniettato dal
    composition root, fallback compat). Import lazy: il router importa
    questo modulo a module-level, l'inverso creerebbe un ciclo."""
    from app.core.orchestrator.router import _project_service

    return _project_service()


def assert_project_group(project_id: str, current_user: CurrentUser) -> None:
    """Verify that the current user can access the project."""
    meta = _get_project_service().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.PROJECT_NOT_FOUND,
                "message": f"Progetto '{project_id}' non trovato",
            },
        )
    assert_group_access(meta, current_user, f"Progetto '{project_id}'")


def assert_task_group(task_id: str, current_user: CurrentUser, *, logger: Any) -> None:
    """Verify that the current user can access a task by its project binding."""
    from app.core.orchestrator.task_service import task_service

    try:
        task_info = task_service.get_task_status(task_id)
    except Exception as e:
        logger.warning(
            f"[GESTITO] _assert_task_group: lookup task_id={task_id} fallito: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": f"Task '{task_id}' non trovato",
            },
        )

    project_id = getattr(task_info, "project_id", None)
    if not project_id and task_info.result:
        project_id = task_result_project_id(task_info.result)

    if not project_id:
        if current_user.role not in ("admin", "superadmin"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": "Task non trovato",
                },
            )
        return

    meta = _get_project_service().get_project_metadata(project_id)
    if meta:
        assert_group_access(meta, current_user, f"Task '{task_id}'")
    elif project_id.startswith("exec_"):
        from app.core.eseguibili.router import _check_visible

        record = eseguibili_db.get_eseguibile(project_id)
        if record:
            _check_visible(record, current_user)
        elif current_user.role not in ("admin", "superadmin"):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.TASK_NOT_FOUND,
                    "message": "Task non trovato",
                },
            )
    elif current_user.role not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": "Task non trovato",
            },
        )


def assert_eseguibile_task_owner(
    task_id: str,
    current_user: CurrentUser,
    *,
    expected_eseguibile_id: str | None = None,
    expected_group_id: str | None = None,
) -> None:
    """Validate task-scoped eseguibile output ownership."""
    owner_group_id = expected_group_id
    if not owner_group_id and expected_eseguibile_id:
        record = eseguibili_db.get_eseguibile(expected_eseguibile_id)
        owner_group_id = str(record.get("group_id") or "") if record else ""
    if not owner_group_id:
        raise HTTPException(status_code=404, detail="Tenant output non determinabile")
    owner_meta = eseguibili_output_store.load_task_owner(
        task_id, group_id=owner_group_id,
    )
    if owner_meta is None:
        if current_user.role != "superadmin":
            raise HTTPException(
                status_code=404,
                detail="Output non trovato o scaduto (ownership metadata assente)",
            )
        return

    if expected_eseguibile_id and owner_meta.get("eseguibile_id") != expected_eseguibile_id:
        raise HTTPException(status_code=404, detail="Task non appartiene a questo eseguibile")
    if owner_meta.get("user_id") != current_user.user_id and current_user.role != "superadmin":
        raise HTTPException(
            status_code=403,
            detail="Output non accessibile (task eseguito da un altro utente)",
        )
