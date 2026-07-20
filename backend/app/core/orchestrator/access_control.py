"""Access-control use-cases for orchestrator HTTP handlers."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout

from app.core.auth.ownership import assert_group_access
from app.core.eseguibili import db as eseguibili_db
from app.core.eseguibili import output_store as eseguibili_output_store
from app.core.orchestrator.router_mappers import (
    task_result_project_id,
    task_result_to_dict,
)
from app.core.projects.service import ProjectService
from app.shared import error_codes
from app.shared.contracts.auth import CurrentUser
from app.core.orchestrator.task_metadata_store import TaskMetadataStoreUnavailable


TASK_LOOKUP_ERRORS = (
    TaskMetadataStoreUnavailable,
    PostgresError,
    PoolTimeout,
    OSError,
)


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


def _task_store_unavailable(task_id: str, *, logger: Any, exc: Exception) -> None:
    logger.error(
        "task access lookup unavailable task_id=%s error=%s",
        task_id,
        type(exc).__name__,
        exc_info=True,
    )
    raise HTTPException(
        status_code=503,
        detail={
            "error": error_codes.TASK_STORE_UNAVAILABLE,
            "message": "Stato task temporaneamente non disponibile",
        },
    ) from exc


def assert_task_group(task_id: str, current_user: CurrentUser, *, logger: Any) -> Any:
    """Resolve and authorize one task, including executable ownership."""
    from app.core.orchestrator.task_service import task_service

    try:
        task_info = task_service.get_task_status(task_id)
    except TASK_LOOKUP_ERRORS as e:
        _task_store_unavailable(task_id, logger=logger, exc=e)

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
        return task_info

    subject_kind = str(getattr(task_info, "subject_kind", "") or "")
    result_payload = task_result_to_dict(task_info.result)
    is_executable = (
        subject_kind == "executable"
        or bool(result_payload.get("is_eseguibile"))
    )
    if is_executable:
        from app.core.eseguibili.router import _check_visible

        executable_id = str(getattr(task_info, "subject_id", None) or project_id)
        try:
            record = eseguibili_db.get_eseguibile(executable_id)
        except TASK_LOOKUP_ERRORS as exc:
            _task_store_unavailable(task_id, logger=logger, exc=exc)
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
        owner_user_id = getattr(task_info, "owner_user_id", None)
        if owner_user_id:
            if (
                str(owner_user_id) != current_user.user_id
                and current_user.role != "superadmin"
            ):
                raise HTTPException(
                    status_code=404,
                    detail={
                        "error": error_codes.TASK_NOT_FOUND,
                        "message": "Task non trovato",
                    },
                )
        elif record:
            assert_eseguibile_task_owner(
                task_id,
                current_user,
                expected_eseguibile_id=executable_id,
                expected_group_id=str(
                    getattr(task_info, "group_id", None)
                    or record.get("group_id")
                    or ""
                ),
            )
        return task_info

    try:
        meta = _get_project_service().get_project_metadata(project_id)
    except TASK_LOOKUP_ERRORS as exc:
        _task_store_unavailable(task_id, logger=logger, exc=exc)
    if meta:
        assert_group_access(meta, current_user, f"Task '{task_id}'")
    elif current_user.role not in ("admin", "superadmin"):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.TASK_NOT_FOUND,
                "message": "Task non trovato",
            },
        )
    return task_info


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
