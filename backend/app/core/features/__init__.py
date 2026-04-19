"""
Modulo Features — Connettore ArcGIS Feature Service per PLENORA-ETL.

Fornisce lettura/scrittura bidirezionale verso ArcGIS Server, Enterprise e Online
tramite REST API dirette (senza dipendenza dal pacchetto arcgis Python).

Componenti:
- models.py           → Modelli Pydantic (connessioni, query, risultati)
- arcgis_client.py    → Client REST puro (auth, browse, query, write)
- features_service.py → CRUD connessioni + orchestrazione operazioni
- routes.py           → Endpoint FastAPI

Uso:
    # Registra le route nel main FastAPI
    from app.core.features.routes import router as features_router
    app.include_router(features_router)
"""

from app.core.features.features_service import FeaturesService, create_features_service
from app.core.features.arcgis_client import ArcGISClient, create_client

__all__ = [
    "FeaturesService",
    "create_features_service",
    "ArcGISClient",
    "create_client",
]
