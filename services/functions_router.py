"""
functions_router.py
API endpoints per le funzioni di colonne calcolate
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Dict, List, Any, Optional

from services.sql_functions import (
    get_function_catalog,
    translate_calculated_column,
    get_translator,
    FUNCTION_CATALOG,
    FunctionCategory
)

router = APIRouter(prefix="/api/functions", tags=["functions"])


class TranslateRequest(BaseModel):
    """Richiesta traduzione espressione"""
    db_type: str
    column_name: str
    expression: dict
    table_alias: Optional[str] = None


class TranslateResponse(BaseModel):
    """Risposta traduzione"""
    sql: str


@router.get("/catalog")
async def get_catalog() -> Dict[str, Any]:
    """
    Restituisce il catalogo completo delle funzioni disponibili.
    Usato dal frontend per popolare il builder.
    """
    catalog = get_function_catalog()
    
    # Raggruppa per categoria
    by_category = {
        "geometric": [],
        "numeric": [],
        "string": [],
        "date": []
    }
    
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
            "date": {"label": "📅 Data", "color": "#FF9800"}
        }
    }


@router.post("/translate", response_model=TranslateResponse)
async def translate_expression(request: TranslateRequest) -> TranslateResponse:
    """
    Traduce un'espressione astratta in SQL per un tipo di database specifico.
    Usato per la preview SQL nel frontend.
    """
    try:
        sql = translate_calculated_column(
            db_type=request.db_type,
            column_name=request.column_name,
            expression=request.expression,
            table_alias=request.table_alias
        )
        return TranslateResponse(sql=sql)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Errore traduzione: {str(e)}")


@router.get("/supported/{db_type}")
async def get_supported_functions(db_type: str) -> Dict[str, Any]:
    """
    Restituisce le funzioni supportate per un tipo di database specifico.
    """
    valid_db_types = ["postgres", "mssql", "mysql", "oracle", "sqlite"]
    
    if db_type not in valid_db_types:
        raise HTTPException(
            status_code=400, 
            detail=f"Tipo DB non valido. Usa: {', '.join(valid_db_types)}"
        )
    
    translator = get_translator(db_type)
    catalog = get_function_catalog()
    
    supported = {}
    unsupported = []
    
    for name, func in catalog.items():
        method_name = f"_translate_{name.lower()}"
        if hasattr(translator, method_name):
            supported[name] = func
        else:
            unsupported.append(name)
    
    return {
        "db_type": db_type,
        "supported": supported,
        "unsupported": unsupported
    }


@router.post("/preview")
async def preview_sql(
    db_type: str,
    expressions: List[dict],
    table_name: str,
    table_alias: Optional[str] = None
) -> Dict[str, Any]:
    """
    Genera una preview della SELECT con tutte le colonne calcolate.
    """
    try:
        calculated_cols = []
        
        for expr in expressions:
            col_name = expr.get("name", "calculated")
            col_expr = expr.get("expression", {})
            
            sql = translate_calculated_column(
                db_type=db_type,
                column_name=col_name,
                expression=col_expr,
                table_alias=table_alias
            )
            calculated_cols.append(sql)
        
        alias = table_alias or table_name[0].lower()
        
        # Costruisci SELECT preview
        select_parts = [f"{alias}.*"] + calculated_cols
        preview = f"SELECT\n  " + ",\n  ".join(select_parts)
        preview += f"\nFROM {table_name} {alias}"
        
        return {
            "preview": preview,
            "calculated_columns": calculated_cols
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))