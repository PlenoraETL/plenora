"""Superadmin-only runtime object-storage inspection endpoints."""

from __future__ import annotations

from typing import Literal
from typing_extensions import TypedDict

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.auth.dependencies import require_superadmin
from app.core.io.object_storage import get_runtime_object_storage
from app.core.io.http_streaming import (
    CloseableStreamingResponse,
    safe_download_filename,
)
from app.core.io.tenant_namespace import (
    TenantStorageKeyError,
    assert_runtime_key,
    legacy_domain,
    parse_tenant_key,
)
from app.observability.audit_action import audit_action
from app.shared.contracts.auth import CurrentUser

from .service import (
    StorageObjectPage,
    StorageProjectCatalog,
    delete_preview_objects,
    list_project_results,
    list_runtime_objects,
)


router = APIRouter(
    prefix="/admin/object-storage",
    tags=["admin-object-storage"],
)


class PreviewDeleteResponse(TypedDict):
    deleted: bool
    deleted_count: int
    scope: str


class ObjectDeleteResponse(TypedDict):
    deleted: bool
    key: str


@router.get("/projects", response_model=StorageProjectCatalog)
def project_results(
    group_id: str | None = Query(None),
    current_user: CurrentUser = Depends(require_superadmin),
) -> StorageProjectCatalog:
    return list_project_results(group_id=group_id)


@router.delete("/previews")
@audit_action(action="object_storage.previews.delete_all")
def delete_all_previews(
    scope: Literal["canonical", "legacy"] = Query("canonical"),
    group_id: str | None = Query(None),
    current_user: CurrentUser = Depends(require_superadmin),
) -> PreviewDeleteResponse:
    try:
        deleted = delete_preview_objects(scope=scope, group_id=group_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Cancellazione preview non riuscita",
        ) from exc
    return {"deleted": True, "deleted_count": deleted, "scope": scope}


@router.get("", response_model=StorageObjectPage)
def list_objects(
    scope: Literal["canonical", "legacy"] = Query("canonical"),
    group_id: str | None = Query(None),
    domain: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    cursor: str | None = Query(None),
    current_user: CurrentUser = Depends(require_superadmin),
) -> StorageObjectPage:
    try:
        return list_runtime_objects(
            scope=scope,
            group_id=group_id,
            domain=domain,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Filtri object storage non validi",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Object storage non disponibile: {type(exc).__name__}",
        ) from exc


@router.get("/download")
@audit_action(action="object_storage.download", target_param="key")
def download_object(
    key: str = Query(..., min_length=1, max_length=2048),
    current_user: CurrentUser = Depends(require_superadmin),
) -> StreamingResponse:
    try:
        assert_runtime_key(key, allow_legacy=True)
    except TenantStorageKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail="Chiave object storage non valida",
        ) from exc
    try:
        opened = get_runtime_object_storage().get_object_stream(key)
    except Exception as exc:
        raise HTTPException(status_code=404, detail="Oggetto non trovato") from exc
    filename = safe_download_filename(key, fallback="object.bin")
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    size = int(opened.get("size") or 0)
    if size:
        headers["Content-Length"] = str(size)
    return CloseableStreamingResponse(
        opened["body"],
        media_type=opened.get("content_type") or "application/octet-stream",
        headers=headers,
    )


def _assert_object_key_not_prefix(key: str) -> None:
    """Reject namespace roots: this endpoint deletes one exact object only."""
    parsed = parse_tenant_key(key)
    if parsed is not None:
        if parsed.relative_segments:
            return
        raise TenantStorageKeyError("chiave object storage senza risorsa")

    domain = legacy_domain(key)
    if domain is not None and key.startswith(f"{domain}/") and key != f"{domain}/":
        return
    raise TenantStorageKeyError("chiave object storage senza risorsa")


@router.delete("")
@audit_action(action="object_storage.delete", target_param="key")
def delete_object(
    key: str = Query(..., min_length=1, max_length=2048),
    current_user: CurrentUser = Depends(require_superadmin),
) -> ObjectDeleteResponse:
    """Delete one exact runtime object without a metadata cascade.

    This is an operator-level emergency/cleanup action. Domain metadata is not
    cascaded and can remain dangling until its normal retention cleanup runs.
    """
    try:
        assert_runtime_key(key, allow_legacy=True)
        _assert_object_key_not_prefix(key)
    except TenantStorageKeyError as exc:
        raise HTTPException(
            status_code=400,
            detail="Chiave object storage non valida",
        ) from exc

    try:
        get_runtime_object_storage().delete(key)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Cancellazione object storage non riuscita",
        ) from exc

    return {"deleted": True, "key": key}


__all__ = ["router"]
