"""
ASGI entry point for PLENORA-ETL.

The application is built by `app.app_factory.create_app`; this module keeps
the historical `app.main:app` import path and runtime service re-exports.
"""

from __future__ import annotations

from app.app_factory import create_app
from app.app_factory import _compute_doc_endpoints as _factory_compute_doc_endpoints


_compute_doc_endpoints = _factory_compute_doc_endpoints


app = create_app()

_runtime_services = app.state.runtime_services

project_service = _runtime_services.project_service
connection_service = _runtime_services.connection_service
database_inspector = _runtime_services.database_inspector
query_generator = _runtime_services.query_generator
query_executor = _runtime_services.query_executor
features_service = _runtime_services.features_service
orchestrator = _runtime_services.orchestrator
api_connection_service = _runtime_services.api_connection_service
datasource_service = _runtime_services.datasource_service


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
