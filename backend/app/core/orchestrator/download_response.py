"""Download response helpers for orchestrator endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.core.results import blob as results_blob
from app.core.results import db as results_db
from app.core.results import legacy_file_store
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http


MIME_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "json": "application/json",
    "geojson": "application/geo+json",
    "gpkg": "application/geopackage+sqlite3",
    "shp": "application/zip",
    "gdb": "application/zip",
    "zip": "application/zip",
    "parquet": "application/octet-stream",
}


def mime_for_filename(filename: str) -> str:
    """Return a content type based on file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return MIME_TYPES.get(ext, "application/octet-stream")


def project_result_response(
    project_id: str,
    *,
    requested_file: Optional[str],
    results_root: str | None = None,
) -> Response:
    """Build the download response for a project result."""
    root = results_root or settings.RESULTS_DIR
    if requested_file:
        try:
            target = legacy_file_store.resolve_specific_file(
                project_id,
                requested_file,
                root=root,
            )
        except ValueError:
            raise_typed_http(
                code=error_codes.ACCESS_DENIED,
                status_code=403,
                message="Accesso negato",
            )
        safe_name = target.name if target else requested_file
        if target is None:
            raise HTTPException(
                404,
                f"File '{safe_name}' non trovato per progetto '{project_id}'",
            )
        return FileResponse(
            path=str(target),
            filename=safe_name,
            media_type=mime_for_filename(safe_name),
        )

    latest_file = legacy_file_store.resolve_latest_file(project_id, root=root)
    if latest_file is not None:
        return FileResponse(
            path=str(latest_file),
            filename=latest_file.name,
            media_type=mime_for_filename(latest_file.name),
        )

    latest = results_db.get_latest_result_row(project_id)
    if not latest:
        raise HTTPException(404, f"Nessun risultato trovato per progetto '{project_id}'")
    if not latest.id:
        raise HTTPException(
            404,
            f"Metadata risultato non valide per progetto '{project_id}'",
        )

    blob = results_blob.get_result_data(latest.id)
    if blob is None:
        raise HTTPException(
            404,
            f"Blob risultato non disponibile per progetto '{project_id}'",
        )

    filename = latest.filename or f"{project_id}.{latest.format or 'csv'}"
    return Response(
        content=blob,
        media_type=mime_for_filename(filename),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(blob)),
        },
    )
