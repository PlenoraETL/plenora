"""Download response helpers for orchestrator endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException
from fastapi.responses import Response

from app.core.results import blob as results_blob
from app.core.results import db as results_db


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
    latest = results_db.get_latest_result_row(project_id)

    if latest and latest.id:
        filename = latest.filename or f"{project_id}.{latest.format or 'csv'}"
        if requested_file and requested_file != filename:
            raise HTTPException(
                404,
                f"File '{requested_file}' non trovato per progetto '{project_id}'",
            )
        blob = results_blob.get_result_data(latest.id)
        if blob is not None:
            return Response(
                content=blob,
                media_type=mime_for_filename(filename),
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Length": str(len(blob)),
                },
            )
        raise HTTPException(
            404,
            f"Blob risultato non disponibile per progetto '{project_id}'",
        )

    if requested_file:
        raise HTTPException(
            410,
            "Download legacy da RESULTS_DIR rimosso: usare result_id/blob "
            "come sorgente canonica",
        )

    if latest and not latest.id:
        raise HTTPException(
            404,
            f"Metadata risultato non valide per progetto '{project_id}'",
        )
    raise HTTPException(
        404,
        f"Nessun risultato blob trovato per progetto '{project_id}'",
    )
