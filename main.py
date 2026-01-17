from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import shutil
from config import settings
from utils.logger import get_logger

from models import (
    TableCatalog, ProjectCreate, ProjectList, CanvasState,
    ConnectionCreate, ConnectionUpdate, ConnectionList, DatabaseConnection, ConnectionTestResult,ExecuteRequest
)
from services.excel_service import ExcelService
from services.project_service import ProjectService
from services.connection_service import ConnectionService
from services.database_inspector import DatabaseInspector
from services.query_generator import QueryGenerator
from services.query_executor import QueryExecutor
from services.orchestrator_service import OrchestratorService
from services.api_connection_service import api_connection_service
from services.functions_router import router as functions_router
from services.schema_router import router as schema_router, init_schema_service

logger = get_logger(__name__)

app = FastAPI(title="Canvas Query Builder API")

# Registra router
app.include_router(functions_router)
app.include_router(schema_router)

# CORS per frontend React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Inizializza servizi
excel_service = ExcelService()
project_service = ProjectService()
connection_service = ConnectionService()
database_inspector = DatabaseInspector()
query_generator = QueryGenerator()
query_executor = QueryExecutor()
project_service = ProjectService()

orchestrator = OrchestratorService(
    project_service=project_service,
    executor=query_executor,
    generator=query_generator,
    connection_service=connection_service
)

init_schema_service(project_service)

# Cartella upload temporanei
UPLOAD_DIR = settings.UPLOADS_DIR


@app.get("/")
def root():
    """Health check"""
    return {"status": "ok", "service": "Canvas Query Builder API"}


@app.post("/api/catalog/upload", response_model=TableCatalog)
async def upload_catalog(file: UploadFile = File(...)):
    """
    Upload file Excel con tabelle del DB
    Expected columns: TABLE_NAME, COLUMN_NAME, COLUMN_TYPE (opzionale)
    
    COLUMN_TYPE può essere: int, real, str, bool, date, geometry, 
    geometry:point, geometry:line, geometry:polygon, etc.
    Se non specificato, default a 'str'.
    """
    # Verifica estensione
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "File deve essere Excel (.xlsx o .xls)")
    
    # Salva file temporaneamente
    temp_path = UPLOAD_DIR / file.filename
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    try:
        # Parse Excel
        catalog = excel_service.parse_excel(str(temp_path))
        
        # Salva catalogo
        excel_service.save_catalog(catalog)
        
        return catalog
    
    except ValueError as e:
        raise HTTPException(400, str(e))
    
    except Exception as e:
        raise HTTPException(500, f"Errore parsing Excel: {str(e)}")
    
    finally:
        # Pulisci file temporaneo
        if temp_path.exists():
            temp_path.unlink()


@app.get("/api/catalog/tables", response_model=TableCatalog)
def get_catalog():
    """Ottieni catalogo completo tabelle"""
    return excel_service.load_catalog()


@app.get("/api/catalog/tables/{table_name}")
def get_table_columns(table_name: str):
    """Ottieni colonne di una tabella specifica"""
    columns = excel_service.get_table_columns(table_name)
    
    if not columns:
        raise HTTPException(404, f"Tabella '{table_name}' non trovata")
    
    return {"table_name": table_name, "columns": columns}


# ==================== CONNESSIONI DATABASE ====================

@app.post("/api/connections", response_model=DatabaseConnection)
def create_connection(data: ConnectionCreate):
    """Crea nuova connessione database"""
    connection = connection_service.create_connection(data)
    return connection


@app.get("/api/connections", response_model=ConnectionList)
def list_connections():
    """Lista tutte le connessioni"""
    return connection_service.list_connections()


@app.get("/api/connections/{connection_id}", response_model=DatabaseConnection)
def get_connection(connection_id: str):
    """Ottieni connessione specifica"""
    connection = connection_service.get_connection(connection_id)
    if not connection:
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    return connection


@app.put("/api/connections/{connection_id}", response_model=DatabaseConnection)
def update_connection(connection_id: str, data: ConnectionUpdate):
    """Aggiorna connessione esistente"""
    connection = connection_service.update_connection(connection_id, data)
    if not connection:
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    return connection


@app.delete("/api/connections/{connection_id}")
def delete_connection(connection_id: str):
    """Elimina una connessione"""
    success = connection_service.delete_connection(connection_id)
    if not success:
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    return {"status": "deleted", "connection_id": connection_id}


@app.post("/api/connections/{connection_id}/test", response_model=ConnectionTestResult)
def test_connection(connection_id: str):
    """Testa una connessione database"""
    result = connection_service.test_connection(connection_id)
    return result


@app.get("/api/connections/{connection_id}/tables", response_model=TableCatalog)
def get_connection_tables(connection_id: str):
    """Ottieni tabelle e colonne direttamente dal database connesso"""
    # Carica connessione
    connection = connection_service.get_connection(connection_id)
    if not connection:
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    
    try:
        # Ispeziona database
        tables = database_inspector.get_tables_and_columns(connection)
        return TableCatalog(tables=tables)
    
    except Exception as e:
        raise HTTPException(500, f"Errore ispezionando database: {str(e)}")


# ==================== PROGETTI ====================

@app.post("/api/projects", response_model=dict)
def create_project(data: ProjectCreate):
    """Crea nuovo progetto"""
    metadata = project_service.create_project(data)
    canvas = project_service.get_project_canvas(metadata.id)
    
    return {
        "metadata": metadata.model_dump(),
        "canvas": canvas.model_dump()
    }


@app.get("/api/projects", response_model=ProjectList)
def list_projects():
    """Lista tutti i progetti"""
    return project_service.list_projects()


@app.get("/api/projects/{project_id}", response_model=dict)
def get_project(project_id: str):
    """Ottieni progetto specifico (metadata + canvas)"""
    metadata = project_service.get_project_metadata(project_id)
    if not metadata:
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    
    canvas = project_service.get_project_canvas(project_id)
    
    return {
        "metadata": metadata.model_dump(),
        "canvas": canvas.model_dump() if canvas else None
    }


@app.put("/api/projects/{project_id}/canvas")
def update_project_canvas(project_id: str, canvas: CanvasState):
    """Aggiorna canvas di un progetto"""
    success = project_service.update_canvas(project_id, canvas)
    
    if not success:
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    
    return {"status": "ok", "project_id": project_id}


@app.post("/api/projects/{project_id}/execute")
def execute_project_pipeline(project_id: str, request: ExecuteRequest = None):
    """
    Endpoint evoluto: esegue la pipeline del progetto (Estrai, Manipola o Arricchisci).
    Gestisce automaticamente la cascata di dipendenze.
    
    Parametri:
    - connection_id: (opzionale) per progetti SQL - override connessione
    - force_recalculate: (opzionale) per progetti Manipola - ricalcola cache
    - format: (opzionale) per progetti Arricchisci - formato output
    """
    try:
        # Ottieni metadata per sapere il tipo progetto
        metadata = project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
        
        # ===== ARRICCHISCI =====
        if metadata.project_type == 'arricchisci':
            canvas = project_service.get_project_canvas(project_id)
            if not canvas:
                raise HTTPException(status_code=400, detail="Canvas non trovato")
            
            # Formato output (default csv)
            format = 'csv'
            if request and hasattr(request, 'format') and request.format:
                format = request.format
            
            # Esegui arricchimento
            result = api_connection_service.execute_enrichment(
                project_id=project_id,
                canvas=canvas.model_dump() if hasattr(canvas, 'model_dump') else canvas,
                format=format
            )
            
            if not result.get("success"):
                raise HTTPException(
                    status_code=400, 
                    detail=result.get("error", "Errore esecuzione arricchimento")
                )
            
            return {
                "success": True,
                "project_id": project_id,
                "output_path": result.get("output_file"),
                "message": f"Arricchimento completato: {result.get('rows_processed')} righe processate",
                "rows_count": result.get("rows_processed"),
                "columns": result.get("columns")
            }
        
        # ===== ESTRAI / MANIPOLA (orchestratore esistente) =====
        force_recalculate = False
        if request:
            force_recalculate = request.force_recalculate
            logger.debug(f"force_recalculate ricevuto: {force_recalculate}")
        
        # Chiamiamo l'orchestratore con il parametro force_recalculate
        execution_result = orchestrator.execute_pipeline(
            project_id,
            force_recalculate=force_recalculate
        )
        
        # Gestione errori dall'orchestratore
        if not execution_result.get("success"):
            status_code = 400
            if "non trovato" in execution_result.get("message", "").lower():
                status_code = 404
                
            raise HTTPException(status_code=status_code, detail=execution_result["message"])
        
        # Risposta al frontend
        return {
            "success": True,
            "project_id": project_id,
            "output_path": execution_result.get("output_path"),
            "message": execution_result.get("message"),
            "query": execution_result.get("query"), 
            "rows_count": execution_result.get("rows_count"),
            "columns": execution_result.get("columns")
        }
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Errore critico in esecuzione pipeline: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore interno del server: {str(e)}")

@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    """Elimina un progetto"""
    success = project_service.delete_project(project_id)
    
    if not success:
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    
    return {"status": "deleted", "project_id": project_id}

@app.get("/api/projects/{project_id}/canvas")
async def get_project_canvas(project_id: str):
    """Recupera lo stato del canvas (nodi e collegamenti) per un progetto."""
    try:
        # Utilizza il project_service che hai già nell'Orchestrator
        canvas = project_service.get_project_canvas(project_id)
        if canvas is None:
            # Se il file non esiste ancora, restituiamo un canvas vuoto invece di un errore
            return {"nodes": [], "edges": []}
        return canvas
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

"""
Endpoint FastAPI per API Connections
Da aggiungere a main.py
"""

# ==================== IMPORTS ====================
# Aggiungi questi import in cima a main.py:
#
# from api_connection_service import api_connection_service


# ==================== ENDPOINTS ====================
# Aggiungi questi endpoint in main.py:

# ---------- API CONNECTIONS ----------

@app.get("/api/api-connections")
def list_api_connections():
    """Lista tutte le API connections configurate."""
    connections = api_connection_service.list_api_connections()
    return {"api_connections": connections}


@app.get("/api/api-connections/{api_id}")
def get_api_connection(api_id: str):
    """Ottiene una API connection specifica."""
    connection = api_connection_service.get_api_connection(api_id)
    if not connection:
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return connection


@app.post("/api/api-connections")
def create_api_connection(data: dict):
    """Crea una nuova API connection."""
    try:
        connection = api_connection_service.create_api_connection(data)
        return connection
    except Exception as e:
        raise HTTPException(400, f"Errore creazione: {str(e)}")


@app.put("/api/api-connections/{api_id}")
def update_api_connection(api_id: str, data: dict):
    """Aggiorna una API connection esistente."""
    connection = api_connection_service.update_api_connection(api_id, data)
    if not connection:
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return connection


@app.delete("/api/api-connections/{api_id}")
def delete_api_connection(api_id: str):
    """Elimina una API connection."""
    success = api_connection_service.delete_api_connection(api_id)
    if not success:
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return {"success": True, "message": f"API connection '{api_id}' eliminata"}


@app.post("/api/api-connections/test")
def test_api_connection_temp(data: dict):
    """
    Testa una configurazione API temporanea (per il wizard).
    Body: {
        "url": "http://...",
        "method": "GET",
        "auth": {...},
        "test_params": {"cod_comune": "H501", ...}
    }
    """
    config = {
        "endpoint": {
            "url": data.get("url", ""),
            "method": data.get("method", "GET")
        },
        "auth": data.get("auth", {"type": "none"})
    }
    test_params = data.get("test_params", {})
    
    result = api_connection_service.test_api_connection(config=config, test_params=test_params)
    return result


@app.post("/api/api-connections/{api_id}/test")
def test_api_connection(api_id: str, data: dict):
    """
    Testa una API connection esistente con parametri di test.
    Body: {"test_params": {"cod_comune": "H501", ...}}
    """
    test_params = data.get("test_params", {})
    result = api_connection_service.test_api_connection(api_id=api_id, test_params=test_params)
    return result

# ============================================================
# 1. ENDPOINT per main.py 
#    Aggiungi dopo l'endpoint /api/projects/{project_id}/execute
# ============================================================

@app.get("/api/projects/{project_id}/preview")
def preview_project_data(project_id: str, limit: int = 100):
    """
    Esegue una preview dei dati del progetto (ESTRAI o MANIPOLA).
    Restituisce le prime N righe senza salvare file.
    
    Parametri:
    - limit: numero massimo di righe da restituire (default 100, max 1000)
    """
    try:
        # Limita il massimo
        limit = min(limit, 1000)
        
        # Ottieni metadata per sapere il tipo progetto
        metadata = project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
        
        # Esegui preview tramite orchestrator
        result = orchestrator.execute_preview(project_id, limit=limit)
        
        if not result.get("success"):
            raise HTTPException(
                status_code=400, 
                detail=result.get("message", "Errore generazione preview")
            )
        
        return result
        
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error(f"Errore preview: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore interno: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    import nest_asyncio
    nest_asyncio.apply()
    uvicorn.run(app, host="0.0.0.0", port=8000)