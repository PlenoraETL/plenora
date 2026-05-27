"""
ASGI entry point for PLENORA-ETL.

The application is built by `app.app_factory.create_app`; this module keeps
the deployment-facing `app.main:app` import path.
"""

from __future__ import annotations

from app.app_factory import create_app
from app.app_factory import _compute_doc_endpoints as _factory_compute_doc_endpoints

# API composition is centralized in app_factory:
# from app.core.api import api_router
# app.include_router(api_router, prefix="/api")

_compute_doc_endpoints = _factory_compute_doc_endpoints


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
