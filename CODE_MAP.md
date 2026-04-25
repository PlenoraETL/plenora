# Code Map

| Area | Path | Use |
|---|---|---|
| Backend core | `backend/app/core/__init__.py` | Package marker for core backend modules. |
| Backend core | `backend/app/core/admin_backups/` | Admin backup routes and backup service operations. |
| Backend core | `backend/app/core/admin_system_status/` | Admin system status routes and health/status service logic. |
| Backend core | `backend/app/core/arricchisci/` | Enrichment flows, API connectors, models, parallel execution, and router. |
| Backend core | `backend/app/core/artifacts/` | Artifact resolution, source loading, and artifact type definitions. |
| Backend core | `backend/app/core/auth/` | Authentication, MFA, request ID middleware, shares, and auth middleware. |
| Backend core | `backend/app/core/carica/` | Load/write pipeline logic, DDL generation, idempotency, column mapping, and router. |
| Backend core | `backend/app/core/connections/` | Database connection models and API routes. |
| Backend core | `backend/app/core/datasources/` | Data source adapters for local, FTP, SFTP, SharePoint, factory, models, and router. |
| Backend core | `backend/app/core/eseguibili/` | Executable scanning, storage, service, models, database access, and router. |
| Backend core | `backend/app/core/estrai/` | Extract query generation, inspection, execution, and router. |
| Backend core | `backend/app/core/features/` | ArcGIS feature client, feature service logic, models, and routes. |
| Backend core | `backend/app/core/files/` | File API routes and file-facing models. |
| Backend core | `backend/app/core/imdf/` | IMDF loading, validation, storage, persistence, publishing, and router. |
| Backend core | `backend/app/core/intermediate/` | Intermediate dataset database access, writer, and router. |
| Backend core | `backend/app/core/logs/` | Log routes and run log routes. |
| Backend core | `backend/app/core/manipola/` | Transform execution, plugin execution, transform registry, transform API, and router. |
| Backend core | `backend/app/core/orchestrator/` | Pipeline orchestration service and router. |
| Backend core | `backend/app/core/projects/` | Project routes and project models. |
| Backend core | `backend/app/core/scheduler/` | Schedule services, execution management, policy, concurrency, run catalog, artifacts, logs, models, and router. |
| Backend core | `backend/app/core/tickets/` | Ticket routes and ticket service logic. |
| Backend shared | `backend/app/shared/crypto.py` | Shared cryptographic helpers. |
| Backend shared | `backend/app/shared/logger.py` | Expected shared logger location; current tree does not contain this file. |
| Frontend | `frontend/src/pages/` | Route-level screens and page containers. |
| Frontend | `frontend/src/api/` | Frontend API clients, request helpers, config, and error handling. |
| Frontend | `frontend/src/store/` | Shared frontend state stores. |
| Frontend | `frontend/src/components/` | Reusable UI components, editor nodes, panels, modals, and layout components. |
