"""
Entry point dell'applicazione FastAPI URBY-ETL.

Architettura (post-rework)
==========================
main.py è ora snello: configura l'app, il CORS middleware, inizializza
i servizi shared e registra i router dei moduli autocontenuti.

Ogni modulo in app/core/<modulo>/ contiene:
- models.py:   modelli Pydantic specifici del dominio
- router.py:   endpoint FastAPI
- executor.py: logica di esecuzione
- service.py:  logica di business (dove applicabile)

Moduli registrati:
- connections  → /api/connections
- projects     → /api/projects (CRUD + canvas + schema)
- files        → /api/files
- estrai       → /api/functions, /api/projects/{id}/generate-sql
- manipola     → /api/transforms
- arricchisci  → /api/enrichment, /api/api-connections, /api/templates
- carica       → /api/carica
- features     → /api/features (ArcGIS Feature Service: connessioni, browse, query, write)
- scheduler    → /api/schedules
- orchestrator → /api/projects/{id}/execute|preview|download, /api/tasks

Porte: backend su 8000, frontend React su 5173
"""

import os
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.utils.logger import get_logger
from app.utils.dataframe import save_dataframe

# Verifica prerequisiti di sistema (DATA_DIR scrivibile, ecc.)
# Interrompe l'avvio con messaggio chiaro se la configurazione è incompleta.
settings.validate_startup()

# Inizializza database SQLite (crea tabelle se non esistono)
from app.db.db_service import init_all_dbs
init_all_dbs()

# Auth JWT
from app.core.auth.auth import router as auth_router, groups_router, get_current_user
from app.core.auth.auth_middleware import JWTMiddleware
from app.db.cleanup_middleware import DBConnectionCleanupMiddleware

# Fix SSL certificate issue caused by PostgreSQL
os.environ.pop('REQUESTS_CA_BUNDLE', None)
os.environ.pop('SSL_CERT_FILE', None)
os.environ.pop('CURL_CA_BUNDLE', None)

logger = get_logger(__name__)

# ==================== APP ====================

app = FastAPI(title="URBY-ETL API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS_LIST,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Requested-With"],
    # Permette al frontend di leggere il filename suggerito dal backend nei download.
    # Senza questo header, downloadBlob.js non riesce a usare il Content-Disposition.
    expose_headers=["Content-Disposition"],
)
app.add_middleware(JWTMiddleware)
# DB cleanup deve essere il middleware più ESTERNO (FastAPI esegue in LIFO),
# così le connessioni thread-local vengono chiuse DOPO che tutto il request
# processing è terminato. Previene leak di file descriptor nei thread riusati
# dal pool uvicorn.
app.add_middleware(DBConnectionCleanupMiddleware)


# Global exception handler — garantisce header CORS anche su eccezioni non gestite.
from fastapi import Request
from fastapi.responses import JSONResponse

_CORS_ORIGINS = set(settings.CORS_ORIGINS_LIST)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    origin = request.headers.get("origin", "")
    headers = {}
    if origin in _CORS_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "false"
    logger.error(
        f"Eccezione non gestita: {request.method} {request.url.path} → {exc}",
        exc_info=True
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Errore interno del server"},
        headers=headers,
    )


# ==================== INIZIALIZZA SERVIZI SHARED ====================

from app.services.project_service import ProjectService
from app.services.connection_service import ConnectionService
from app.core.estrai.database_inspector import DatabaseInspector
from app.core.estrai.query_generator import QueryGenerator
from app.db.query_executor import QueryExecutor
from app.core.orchestrator.service import OrchestratorService
from app.core.arricchisci.api_connector import api_connection_service
from app.core.features.features_service import FeaturesService
from app.core.arricchisci.router import executables_router

project_service = ProjectService()
connection_service = ConnectionService()
database_inspector = DatabaseInspector()
query_generator = QueryGenerator()
query_executor = QueryExecutor(results_dir=settings.DATA_DIR / "temp", save_function=save_dataframe)
features_service = FeaturesService(save_function=save_dataframe)

orchestrator = OrchestratorService(
    project_service=project_service,
    executor=query_executor,
    generator=query_generator,
    connection_service=connection_service,
    features_service=features_service
)


# ==================== INIT MODULI ====================

from app.core.connections.router import router as connections_router, init_connections
from app.core.projects.router import router as projects_router, init_projects
from app.core.files.router import router as files_router
from app.core.estrai.router import functions_router
from app.core.manipola.router import router as transforms_router
from app.core.arricchisci.router import (
    enrichment_router, api_connections_router, templates_router,
    enrich_router, init_arricchisci
)
from app.core.carica.router import router as carica_router, init_carica_service
from app.core.features.routes import router as features_router
from app.core.scheduler.router import router as schedules_router
from app.core.orchestrator.router import pipeline_router, tasks_router, init_orchestrator
from app.core.logs.router import router as logs_router
from app.core.logs.runs_router import router as runs_router
from app.core.datasources.router import router as datasources_router, init_datasources
from app.core.intermediate.router import router as intermediate_router
from app.core.datasources.service import datasource_service
from app.core.eseguibili.router import router as eseguibili_router
from app.core.auth.shares_router import router as shares_router

# Inizializza moduli che necessitano di servizi
init_connections(connection_service, database_inspector)
init_projects(project_service)
init_arricchisci(project_service, api_connection_service)
init_datasources(datasource_service)
init_carica_service(connection_service)
init_orchestrator(orchestrator)
# features: usa lazy init interno, nessuna init_* necessaria


# ==================== REGISTRA ROUTER ====================

# Auth (auth in signature di ogni endpoint)
app.include_router(auth_router, prefix="/api")
app.include_router(groups_router, prefix="/api")
app.include_router(shares_router, prefix="/api")

# Connections (auth dichiarata in signature di ogni endpoint)
app.include_router(connections_router, prefix="/api")

# Projects (CRUD + canvas + schema)
app.include_router(projects_router, prefix="/api")

# Files
app.include_router(files_router, prefix="/api")

# Estrai (functions catalog + translate)
app.include_router(functions_router, prefix="/api")

# Manipola (transforms registry)
app.include_router(transforms_router, prefix="/api")

# Arricchisci (enrichment + api-connections + templates)
app.include_router(enrichment_router, prefix="/api")
app.include_router(api_connections_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(enrich_router, prefix="/api")

# Carica
app.include_router(carica_router, prefix="/api")

# Features (ArcGIS Feature Service: connessioni, browse, query, write)
app.include_router(features_router, prefix="/api")

# Scheduler
app.include_router(schedules_router, prefix="/api")

# Orchestrator (execute + preview + download + tasks)
app.include_router(pipeline_router, prefix="/api")
app.include_router(tasks_router, prefix="/api")

# Logs (consultazione dal frontend)
app.include_router(logs_router, prefix="/api")
app.include_router(runs_router, prefix="/api")

# Datasources
app.include_router(datasources_router, prefix="/api")
app.include_router(executables_router, prefix="/api")
app.include_router(intermediate_router, prefix="/api")

# Eseguibili (catene on-demand)
app.include_router(eseguibili_router, prefix="/api")

# ==================== HEALTH CHECK ====================

@app.get("/")
def root():
    """Health check"""
    return {"status": "ok", "service": "URBY-ETL API"}


# ==================== STARTUP ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
