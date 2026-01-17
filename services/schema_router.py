"""
Router per endpoint schema inference.
Aggiungere a main.py: app.include_router(schema_router)
"""

from fastapi import APIRouter, HTTPException
from typing import Optional
from pydantic import BaseModel

router = APIRouter(prefix="/api/projects", tags=["schema"])


# Modelli request/response
class SchemaRequest(BaseModel):
    """Request per inferenza schema singolo nodo"""
    node_id: Optional[str] = None


class ValidationRequest(BaseModel):
    """Request per validazione connessione"""
    source_node_id: str
    target_node_id: str
    config: Optional[dict] = None


# Variabile globale per il service (inizializzata da main.py)
_schema_service = None
_validator = None


def init_schema_service(project_service):
    """Inizializza il service. Chiamare da main.py dopo aver creato project_service."""
    global _schema_service, _validator
    from services.schema_inference_service import SchemaInferenceService, ConnectionValidator
    _schema_service = SchemaInferenceService(project_service)
    _validator = ConnectionValidator(_schema_service)


@router.get("/{project_id}/schemas")
def get_all_schemas(project_id: str):
    """
    Ottiene lo schema (colonne + tipi) per tutti i nodi del progetto.
    
    Response:
    {
        "schemas": {
            "node_1": [{"name": "id", "type": "int", "source_node": "node_1", "is_calculated": false}, ...],
            "node_2": [...],
            ...
        }
    }
    """
    if not _schema_service:
        raise HTTPException(500, "Schema service non inizializzato")
    
    try:
        schemas = _schema_service.infer_all_schemas(project_id)
        return {"schemas": schemas}
    except Exception as e:
        raise HTTPException(500, f"Errore inferenza schema: {str(e)}")


@router.get("/{project_id}/schemas/{node_id}")
def get_node_schema(project_id: str, node_id: str):
    """
    Ottiene lo schema per un singolo nodo.
    
    Response:
    {
        "node_id": "transform_1",
        "columns": [{"name": "id", "type": "int", ...}, ...]
    }
    """
    if not _schema_service:
        raise HTTPException(500, "Schema service non inizializzato")
    
    try:
        columns = _schema_service.infer_node_schema(project_id, node_id)
        return {
            "node_id": node_id,
            "columns": columns
        }
    except Exception as e:
        raise HTTPException(500, f"Errore inferenza schema nodo: {str(e)}")


@router.post("/{project_id}/validate-connection")
def validate_connection(project_id: str, request: ValidationRequest):
    """
    Valida una connessione tra due nodi.
    
    Request:
    {
        "source_node_id": "source_1",
        "target_node_id": "transform_1",
        "config": {"left_on": "id", "right_on": "user_id"}  // opzionale
    }
    
    Response:
    {
        "valid": true,
        "error": null
    }
    """
    if not _validator:
        raise HTTPException(500, "Validator non inizializzato")
    
    try:
        is_valid, error = _validator.validate_connection(
            project_id=project_id,
            source_node_id=request.source_node_id,
            target_node_id=request.target_node_id,
            connection_config=request.config
        )
        return {
            "valid": is_valid,
            "error": error
        }
    except Exception as e:
        raise HTTPException(500, f"Errore validazione: {str(e)}")


@router.get("/{project_id}/validate-all")
def validate_all_connections(project_id: str):
    """
    Valida tutte le connessioni del progetto.
    
    Response:
    {
        "valid": false,
        "errors": [
            {"edge_id": "edge_1", "message": "Colonna 'xyz' non trovata", "severity": "error"},
            ...
        ]
    }
    """
    if not _validator:
        raise HTTPException(500, "Validator non inizializzato")
    
    try:
        errors = _validator.validate_all_connections(project_id)
        return {
            "valid": len(errors) == 0,
            "errors": errors
        }
    except Exception as e:
        raise HTTPException(500, f"Errore validazione: {str(e)}")
