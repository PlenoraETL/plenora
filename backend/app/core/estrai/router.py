"""
Router API per il modulo ESTRAI.

Catalogo funzioni e traduzione espressioni per colonne calcolate.
"""

import re

from fastapi import APIRouter, HTTPException, Depends
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from pydantic import BaseModel
from typing import Optional

from app.db.sql_functions import (
    get_function_catalog,
    translate_calculated_column,
)
from app.core.auth.dependencies import require_not_esecutore
from app.core.auth.ownership import assert_group_access
from app.core.connections.service import ConnectionService
from app.core.projects.service import ProjectService
from app.core.query_execution.query_executor import QueryExecutor
from app.core.estrai.types import JsonObject
from app.shared.contracts.auth import CurrentUser
from app.utils.logger import get_logger

logger = get_logger(__name__)

functions_router = APIRouter(prefix="/functions", tags=["functions"])

# Servizi iniettati dal composition root via ESTRAI_BINDING (Epic D roadmap
# accoppiamento). Il fallback per-chiamata preserva il comportamento storico
# nei contesti senza wiring (unit test su app parziali).
_project_service = None
_connection_service = None
_query_executor = None


def init_estrai(
    project_service: ProjectService,
    connection_service: ConnectionService,
    query_executor: QueryExecutor,
) -> None:
    """Inizializza il modulo. Chiamare da app_factory dopo setup."""
    global _project_service, _connection_service, _query_executor
    _project_service = project_service
    _connection_service = connection_service
    _query_executor = query_executor


def _get_project_service() -> ProjectService:
    return _project_service if _project_service is not None else ProjectService()


def _get_connection_service() -> ConnectionService:
    return _connection_service if _connection_service is not None else ConnectionService()


def _get_query_executor() -> QueryExecutor:
    return _query_executor if _query_executor is not None else QueryExecutor()

# Identificatore SQL semplice (allineato ai validator di sql_functions.py).
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,62}$')


def _safe_table_ref(table_name: str, db_schema: Optional[str]) -> str:
    """Valida (whitelist) un riferimento tabella `nome` o `schema.nome`,
    prependendo lo schema della connessione se il nome è non qualificato.
    Solo identificatori semplici → niente quoting necessario, niente injection."""
    parts = (table_name or "").strip().split(".")
    if len(parts) == 1 and db_schema:
        parts = [db_schema, parts[0]]
    if not parts or len(parts) > 2 or not all(_IDENT_RE.match(p) for p in parts):
        raise ValueError("Nome tabella non valido per l'anteprima")
    return ".".join(parts)


def _limit_one(base_sql: str, db_type: str) -> str:
    """Aggiunge il limite a 1 riga in modo dialect-aware (cfr. executor preview)."""
    if db_type in ('postgresql', 'postgres', 'mysql', 'sqlite'):
        return f"{base_sql} LIMIT 1"
    if db_type == 'mssql':
        return re.sub(r'^SELECT\s+', 'SELECT TOP 1 ', base_sql, count=1, flags=re.IGNORECASE)
    if db_type == 'oracle':
        return f"{base_sql} FETCH FIRST 1 ROWS ONLY"
    raise ValueError(f"Tipo database '{db_type}' non supportato per l'anteprima")


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
async def get_catalog(
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
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
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


class PreviewFormulaRequest(BaseModel):
    """Richiesta anteprima formula su una riga campione reale (#26)."""
    project_id: str
    table_name: str
    db_type: str
    table_alias: Optional[str] = "t"
    column_name: Optional[str] = None
    expression: Optional[dict] = None
    function_name: Optional[str] = None
    params: Optional[dict] = None


@functions_router.post("/preview-formula")
async def preview_formula(
    request: PreviewFormulaRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Valuta una colonna calcolata su UNA riga campione reale del progetto (#26).

    Solo modalità builder (ri-traduce server-side l'espressione → niente SQL
    arbitrario dal client). Identificatori validati a whitelist; group-access
    sul progetto. Errori SQL → {success:false, message} per feedback inline.
    """
    # 1. Espressione → SQL (server-side, sanitizzato da translate_calculated_column).
    if request.expression is not None:
        expr = request.expression
    elif request.function_name is not None:
        expr = {"function": request.function_name, "params": request.params or {}}
    else:
        raise HTTPException(status_code=400, detail="Richiesto 'expression' oppure 'function_name'")

    alias = (request.table_alias or "t").strip() or "t"
    if not _IDENT_RE.match(alias):
        raise HTTPException(status_code=400, detail="Alias tabella non valido")

    try:
        select_sql = translate_calculated_column(
            db_type=request.db_type,
            column_name=request.column_name or "preview_value",
            expression=expr,
            table_alias=alias,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. Progetto + group access (404 se assente, 403 se altro gruppo).
    meta = _get_project_service().get_project_metadata(request.project_id)
    assert_group_access(meta, current_user, f"Progetto '{request.project_id}'")
    if not getattr(meta, "default_connection_id", None):
        return {"success": False, "message": "Connessione non configurata per il progetto"}

    # 3. Connessione database (le sorgenti ArcGIS non hanno anteprima formula SQL).
    connection = _get_connection_service().get_connection(meta.default_connection_id)
    if not connection:
        return {"success": False, "message": "Anteprima formula disponibile solo su connessioni database"}

    # 4. SQL su 1 riga campione (table-direct, identificatori validati).
    try:
        table_ref = _safe_table_ref(request.table_name, getattr(connection, "db_schema", None))
        sql = _limit_one(f"SELECT {select_sql} FROM {table_ref} {alias}", connection.type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 5. Esecuzione: gli errori SQL (funzione inesistente, tipo errato, ...) sono
    #    feedback utile per l'utente → niente 500, ritorniamo il messaggio.
    try:
        rows, _columns = _get_query_executor().execute_raw(connection, sql, {})
    except Exception as e:
        logger.info(f"preview-formula: esecuzione fallita ({type(e).__name__})")
        return {"success": False, "message": str(e)}

    if not rows:
        return {"success": True, "value": None, "empty": True}
    value = rows[0][0]
    return {"success": True, "value": None if value is None else str(value)}
