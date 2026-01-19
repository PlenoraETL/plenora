"""
Main FastAPI application - Canvas Query Builder API

REFACTORED:
- Rimosso project_service duplicato (era linea 46 e 50)
- Rimosso traceback.print_exc() (già loggato con logger.error)
"""

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
import shutil
from config import settings
from utils.logger import get_logger

from models import (
    TableCatalog, ProjectCreate, ProjectList, CanvasState,
    ConnectionCreate, ConnectionUpdate, ConnectionList, 
    DatabaseConnection, ConnectionTestResult, ExecuteRequest
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
from services.file_routes import router as files_router

logger = get_logger(__name__)

app = FastAPI(title="Canvas Query Builder API")

# Registra router
app.include_router(functions_router)
app.include_router(schema_router)
app.include_router(files_router)

# CORS per frontend React
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== INIZIALIZZA SERVIZI ====================
# FIX: Rimosso project_service duplicato
excel_service = ExcelService()
project_service = ProjectService()
connection_service = ConnectionService()
database_inspector = DatabaseInspector()
query_generator = QueryGenerator()
query_executor = QueryExecutor()

orchestrator = OrchestratorService(
    project_service=project_service,
    executor=query_executor,
    generator=query_generator,
    connection_service=connection_service
)

init_schema_service(project_service)

UPLOAD_DIR = settings.UPLOADS_DIR


# ==================== HEALTH CHECK ====================

@app.get("/")
def root():
    """Health check"""
    return {"status": "ok", "service": "Canvas Query Builder API"}


# ==================== CATALOG ====================

@app.post("/api/catalog/upload", response_model=TableCatalog)
async def upload_catalog(file: UploadFile = File(...)):
    """Upload file Excel con tabelle del DB"""
    if not file.filename.endswith(('.xlsx', '.xls')):
        raise HTTPException(400, "File deve essere Excel (.xlsx o .xls)")
    
    temp_path = UPLOAD_DIR / file.filename
    with open(temp_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    
    try:
        catalog = excel_service.parse_excel(str(temp_path))
        excel_service.save_catalog(catalog)
        return catalog
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"Errore parsing Excel: {str(e)}")
    finally:
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
    return connection_service.create_connection(data)


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
    if not connection_service.delete_connection(connection_id):
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    return {"status": "deleted", "connection_id": connection_id}


@app.post("/api/connections/{connection_id}/test", response_model=ConnectionTestResult)
def test_connection(connection_id: str):
    """Testa una connessione database"""
    return connection_service.test_connection(connection_id)


@app.get("/api/connections/{connection_id}/tables", response_model=TableCatalog)
def get_connection_tables(connection_id: str):
    """Ottieni tabelle e colonne direttamente dal database"""
    connection = connection_service.get_connection(connection_id)
    if not connection:
        raise HTTPException(404, f"Connessione '{connection_id}' non trovata")
    
    try:
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
    if not project_service.update_canvas(project_id, canvas):
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    return {"status": "ok", "project_id": project_id}


@app.post("/api/projects/{project_id}/execute")
def execute_project_pipeline(project_id: str, request: ExecuteRequest = None):
    """Esegue la pipeline del progetto (Estrai, Manipola o Arricchisci)."""
    try:
        metadata = project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
        
        force_recalculate = request.force_recalculate if request else False
        result = orchestrator.execute_pipeline(project_id, force_recalculate=force_recalculate)
        
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("message", "Errore esecuzione"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore execute_project_pipeline: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/projects/{project_id}/preview")
def preview_project(project_id: str, limit: int = 100):
    """Preview dei dati del progetto senza salvare file."""
    try:
        result = orchestrator.execute_preview(project_id, limit=limit)
        
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("message", "Errore preview"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore preview_project: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str):
    """Elimina un progetto"""
    if not project_service.delete_project(project_id):
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    return {"status": "deleted", "project_id": project_id}


@app.get("/api/projects/{project_id}/download")
def download_project_result(project_id: str):
    """Scarica il file risultato di un progetto."""
    results_dir = settings.RESULTS_DIR / project_id
    
    if not results_dir.exists():
        raise HTTPException(404, f"Nessun risultato trovato per progetto '{project_id}'")
    
    files = list(results_dir.glob("*.*"))
    if not files:
        raise HTTPException(404, f"Nessun file risultato trovato")
    
    latest_file = max(files, key=lambda x: x.stat().st_mtime)
    
    return FileResponse(
        path=str(latest_file),
        filename=latest_file.name,
        media_type='application/octet-stream'
    )


# ==================== API CONNECTIONS ====================

@app.get("/api/api-connections")
def list_api_connections():
    """Lista tutte le API connections."""
    return api_connection_service.list_api_connections()


@app.get("/api/api-connections/{api_id}")
def get_api_connection(api_id: str):
    """Ottieni una API connection specifica."""
    connection = api_connection_service.get_api_connection(api_id)
    if not connection:
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return connection


@app.post("/api/api-connections")
def create_api_connection(data: dict):
    """Crea una nuova API connection."""
    return api_connection_service.create_api_connection(data)


@app.put("/api/api-connections/{api_id}")
def update_api_connection(api_id: str, data: dict):
    """Aggiorna una API connection esistente."""
    result = api_connection_service.update_api_connection(api_id, data)
    if not result:
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return result


@app.delete("/api/api-connections/{api_id}")
def delete_api_connection(api_id: str):
    """Elimina una API connection."""
    if not api_connection_service.delete_api_connection(api_id):
        raise HTTPException(404, f"API connection '{api_id}' non trovata")
    return {"status": "deleted", "api_id": api_id}


@app.post("/api/api-connections/{api_id}/test")
def test_api_connection(api_id: str, test_params: dict = None):
    """Testa una API connection."""
    return api_connection_service.test_api_connection(api_id=api_id, test_params=test_params)


@app.post("/api/api-connections/test-config")
def test_api_config(config: dict, test_params: dict = None):
    """Testa una configurazione API senza salvarla."""
    return api_connection_service.test_api_connection(config=config, test_params=test_params)


# ==================== SQL GENERATION ====================

@app.post("/api/projects/{project_id}/generate-sql")
def generate_sql(project_id: str):
    """Genera SQL dal canvas di un progetto."""
    metadata = project_service.get_project_metadata(project_id)
    if not metadata:
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    
    canvas = project_service.get_project_canvas(project_id)
    if not canvas:
        raise HTTPException(400, "Canvas non trovato")
    
    result_node = next((n for n in canvas.nodes if n.type == "result"), None)
    if not result_node:
        raise HTTPException(400, "Nodo risultato non trovato nel canvas")
    
    try:
        # Determina db_type dalla connessione
        db_type = "postgres"
        if metadata.default_connection_id:
            connection = connection_service.get_connection(metadata.default_connection_id)
            if connection:
                db_type = connection.type
        
        query_info = query_generator.generate_sql(
            canvas.nodes, 
            canvas.edges, 
            result_node.id,
            db_type=db_type
        )
        return {"sql": query_info['sql'], "params": query_info.get('params', {})}
    except Exception as e:
        logger.error(f"Errore generazione SQL: {e}")
        raise HTTPException(400, str(e))


# ==================== ENRICHMENT ====================

@app.post("/api/projects/{project_id}/enrich")
def execute_enrichment(project_id: str, request: ExecuteRequest = None):
    """Esegue l'arricchimento di un progetto ARRICCHISCI."""
    try:
        metadata = project_service.get_project_metadata(project_id)
        if not metadata:
            raise HTTPException(404, f"Progetto '{project_id}' non trovato")
        
        if metadata.project_type != "arricchisci":
            raise HTTPException(400, f"Progetto '{project_id}' non è di tipo 'arricchisci'")
        
        canvas = project_service.get_project_canvas(project_id)
        if not canvas:
            raise HTTPException(400, "Canvas non trovato")
        
        force_recalculate = request.force_recalculate if request else False
        
        result = api_connection_service.execute_enrichment(
            project_id=project_id,
            canvas=canvas.model_dump(),
            force_recalculate=force_recalculate
        )
        
        if not result.get("success"):
            raise HTTPException(400, result.get("error", "Errore arricchimento"))
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Errore execute_enrichment: {e}")
        raise HTTPException(500, str(e))


# ==================== STARTUP ====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)