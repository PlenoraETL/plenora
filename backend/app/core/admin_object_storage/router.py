"""Superadmin-only runtime object-storage inspection endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from app.core.auth.dependencies import require_superadmin
from app.core.io.object_storage import get_runtime_object_storage
from app.core.io.tenant_namespace import TenantStorageKeyError, assert_runtime_key
from app.observability.audit_action import audit_action
from app.shared.contracts.auth import CurrentUser

from .service import StorageObjectPage, list_runtime_objects


router = APIRouter(
    prefix="/admin/object-storage",
    tags=["admin-object-storage"],
)


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
    filename = key.rsplit("/", 1)[-1].replace('"', "") or "object.bin"
    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    size = int(opened.get("size") or 0)
    if size:
        headers["Content-Length"] = str(size)
    return StreamingResponse(
        opened["body"],
        media_type=opened.get("content_type") or "application/octet-stream",
        headers=headers,
    )


__all__ = ["router"]
