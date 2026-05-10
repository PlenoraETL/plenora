"""Plenora E2E fake REST server.

Single-process FastAPI app emulating every external REST pattern plenora
must handle in production: auth flows, pagination flavours, error modes,
rate limiting, GeoJSON, file download/upload, ArcGIS-style endpoints, and
slow / partial responses.
"""

from __future__ import annotations

from fastapi import FastAPI

from routes import (
    arcgis,
    auth,
    data,
    errors,
    files,
    geo,
    pagination,
    rate_limit,
    sharepoint,
    slow,
)

app = FastAPI(
    title="Plenora E2E Fake REST Server",
    description="Single-process fake REST emulating all external API patterns plenora handles.",
    version="1.0.0",
)

app.include_router(auth.router, prefix="/auth", tags=["auth"])
app.include_router(data.router, prefix="/data", tags=["data"])
app.include_router(pagination.router, prefix="/page", tags=["pagination"])
app.include_router(errors.router, prefix="/error", tags=["errors"])
app.include_router(rate_limit.router, prefix="/throttle", tags=["rate_limit"])
app.include_router(geo.router, prefix="/geo", tags=["geo"])
app.include_router(files.router, prefix="/file", tags=["files"])
# ArcGIS routes embed their own /sharing and /services prefixes.
app.include_router(arcgis.router, tags=["arcgis"])
# SharePoint routes embed their own /sharepoint prefix.
app.include_router(sharepoint.router, tags=["sharepoint"])
app.include_router(slow.router, prefix="/slow", tags=["slow"])


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
