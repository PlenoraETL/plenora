"""
Router API per il modulo ESTRAI.

Catalogo funzioni e traduzione espressioni per colonne calcolate.
"""

from fastapi import APIRouter, HTTPException, Depends
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from pydantic import BaseModel
from typing import Dict, Any, Optional

from app.db.sql_functions import (
    get_function_catalog,
    translate_calculated_column,
)
from app.core.auth.dependencies import require_not_esecutore
from app.shared.contracts.auth import CurrentUser
from app.utils.logger import get_logger

logger = get_logger(__name__)

functions_router = APIRouter(prefix="/functions", tags=["functions"])


class TranslateRequest(BaseModel):
    """Richiesta traduzione espressione."""
    db_type: str
    column_name: Optional[str] = None
    expression: Optional[dict] = None
    table_alias: Optional[str] = None
    function_name: Optional[str] = None
    params: Optional[dict] = None


class TranslateResponse(BaseModel):
    """Risposta traduzione"""
    sql: str


@functions_router.get("/catalog")
async def get_catalog(current_user: CurrentUser = Depends(require_not_esecutore)) -> Dict[str, Any]:
    """Catalogo completo funzioni SQL disponibili."""
    catalog = get_function_catalog()
    
    by_category = {"geometric": [], "numeric": [], "string": [], "date": [], "conditional": [], "cast": []}

    for name, func in catalog.items():
        category = func["category"]
        if category in by_category:
            by_category[category].append(func)

    return {
        "functions": catalog,
        "by_category": by_category,
        "categories": {
            "geometric": {"label": "🌐 Geometriche", "color": "#9C27B0"},
            "numeric": {"label": "🔢 Numeriche", "color": "#2196F3"},
            "string": {"label": "📝 Testo", "color": "#4CAF50"},
            "date": {"label": "📅 Data", "color": "#FF9800"},
            "conditional": {"label": "🔀 Condizionali", "color": "#E91E63"},
            "cast": {"label": "🔄 Conversione", "color": "#607D8B"}
        }
    }


@functions_router.post("/translate", response_model=TranslateResponse)
async def translate_expression(request: TranslateRequest, current_user: CurrentUser = Depends(require_not_esecutore)) -> TranslateResponse:
    """Traduce un'espressione astratta in SQL per un tipo di database specifico."""
    try:
        if request.expression is not None:
            expression = request.expression
            column_name = request.column_name or "calculated"
        elif request.function_name is not None:
            expression = {
                "function": request.function_name,
                "params": request.params or {}
            }
            column_name = request.column_name or "calculated"
        else:
            raise HTTPException(
                status_code=400, 
                detail="Richiesto 'expression' oppure 'function_name' + 'params'"
            )
        
        sql = translate_calculated_column(
            db_type=request.db_type,
            column_name=column_name,
            expression=expression,
            table_alias=request.table_alias
        )
        return TranslateResponse(sql=sql)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")
