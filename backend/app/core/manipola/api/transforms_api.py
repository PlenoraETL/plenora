"""
Endpoint API per il registry trasformazioni MANIPOLA.

Espone il transforms_registry.json al frontend React.

Endpoints:
- GET /api/transforms/registry → registry completo (trasformazioni + categorie)
"""

from fastapi import APIRouter
from typing import Dict, Any

from app.core.manipola.plugin_executor import get_executor

router = APIRouter(prefix="/transforms", tags=["transforms"])


@router.get("/registry")
def get_registry() -> Dict[str, Any]:
    """
    Ritorna il registry completo delle trasformazioni.

    Returns:
        Dict con:
        - transforms: dizionario trasformazioni
        - categories: categorie disponibili
    """
    executor = get_executor()

    return {
        "transforms": executor.get_registry(),
        "categories": executor.get_categories(),
        "version": "1.0"
    }
