"""API router composition for PLENORA-ETL."""

from fastapi import APIRouter

from app.core.admin_backups.router import router as admin_backups_router
from app.core.admin_runtime_config.router import router as admin_runtime_config_router
from app.core.admin_system_status.router import router as admin_system_status_router
from app.core.admin_object_storage.router import router as admin_object_storage_router
from app.core.arricchisci.router import (
    api_connections_router,
    enrich_router,
    enrichment_router,
    templates_router,
)
from app.core.auth.router import router as auth_router
from app.core.auth.users_router import users_router
from app.core.auth.groups_router import groups_router
from app.core.auth.shares_router import router as shares_router
from app.core.carica.router import router as carica_router
from app.core.connections.router import router as connections_router
from app.core.datasources.router import router as datasources_router
from app.core.eseguibili.router import router as eseguibili_router
from app.core.estrai.router import functions_router
from app.core.features.routes import router as features_router
from app.core.files.router import router as files_router
from app.core.intermediate.router import router as intermediate_router
from app.core.logs.router import router as logs_router
from app.core.logs.runs_router import router as runs_router
from app.core.manipola.api.transforms_api import router as transforms_router
from app.core.orchestrator.router import pipeline_router, tasks_router
from app.core.projects.router import router as projects_router
from app.core.scheduler.router import router as schedules_router
from app.core.tickets.router import router as tickets_router

api_router = APIRouter()

# Auth.
api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(groups_router)
api_router.include_router(shares_router)

# Connections.
api_router.include_router(connections_router)

# Projects: CRUD, canvas e schema.
api_router.include_router(projects_router)

# Files.
api_router.include_router(files_router)

# Estrai: catalogo funzioni e traduzione query.
api_router.include_router(functions_router)

# Manipola: registry trasformazioni.
api_router.include_router(transforms_router)

# Arricchisci: enrichment, api-connections e template.
api_router.include_router(enrichment_router)
api_router.include_router(api_connections_router)
api_router.include_router(templates_router)
api_router.include_router(enrich_router)

# Carica.
api_router.include_router(carica_router)

# Features: ArcGIS Feature Service, browse, query e write.
api_router.include_router(features_router)

# Scheduler.
api_router.include_router(schedules_router)

# Orchestrator: execute, preview, download e task.
api_router.include_router(pipeline_router)
api_router.include_router(tasks_router)

# Logs: consultazione dal frontend.
api_router.include_router(logs_router)
api_router.include_router(runs_router)

# Datasources.
api_router.include_router(datasources_router)
api_router.include_router(intermediate_router)

# Eseguibili: catene on-demand.
api_router.include_router(eseguibili_router)

# Admin Backups: superadmin only.
api_router.include_router(admin_backups_router)

# Admin System Status dashboard: superadmin only.
api_router.include_router(admin_system_status_router)
api_router.include_router(admin_object_storage_router)

# Admin Runtime Config: superadmin only, read-only.
api_router.include_router(admin_runtime_config_router)

# Tickets: tech debt e user support, RBAC nel router.
api_router.include_router(tickets_router)
