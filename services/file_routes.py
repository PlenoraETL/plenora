"""
Router per gestione File Source.

Endpoints:
    POST   /api/files/upload          - Upload nuovo file
    GET    /api/files                  - Lista file sources
    GET    /api/files/{file_id}        - Dettaglio file source
    PUT    /api/files/{file_id}        - Aggiorna file source
    DELETE /api/files/{file_id}        - Elimina file source
    GET    /api/files/{file_id}/layers - Lista layers/sheets
    GET    /api/files/{file_id}/preview - Preview dati
    
Aggiungi a main.py:
    from services.file_routes import router as files_router
    app.include_router(files_router)
"""

from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query
from typing import Optional

from services.file_source_service import file_source_service
from models import (
    FileSource,
    FileSourceList,
    FileSourceUpdate,
    FileSourceLayersResponse,
    FileSourcePreview
)
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/files", tags=["File Sources"])


@router.post("/preview-temp")
async def preview_file_temp(
    file: UploadFile = File(...)
):
    """
    Preview temporanea di un file per ottenere le colonne disponibili.
    Non salva il file, lo legge solo per estrarre info colonne.
    
    Usato nel frontend per mostrare le colonne selezionabili
    prima di confermare l'upload.
    
    Returns:
        - columns: Lista colonne con nome e tipo
        - row_count: Numero totale righe
        - format: Formato rilevato
    """
    try:
        result = file_source_service.preview_file_temp(
            file_content=file.file,
            filename=file.filename
        )
        return result
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore preview temp: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore lettura file: {str(e)}")


@router.post("/upload", response_model=FileSource)
async def upload_file(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(""),
    layer: Optional[str] = Form(None),
    sheet: Optional[str] = Form(None),
    selected_columns: Optional[str] = Form(None)
):
    """
    Upload un nuovo file e crea un File Source.
    
    Formati supportati:
    - CSV (.csv)
    - Excel (.xlsx, .xls)
    - JSON (.json)
    - GeoJSON (.geojson)
    - GeoPackage (.gpkg)
    - Shapefile (.zip contenente .shp)
    - FileGDB (.zip contenente .gdb)
    
    Per GDB/GPKG con più layer, specifica il layer da usare.
    Per Excel con più fogli, specifica lo sheet da usare.
    Se non specificato e c'è un solo layer/sheet, viene usato automaticamente.
    
    Parametri opzionali:
    - selected_columns: JSON array di nomi colonne da importare.
      Se non specificato, importa tutte le colonne.
      Esempio: '["id", "nome", "geometry"]'
    """
    try:
        # Verifica estensione
        filename = file.filename
        valid_extensions = ['.csv', '.xlsx', '.xls', '.json', '.geojson', '.gpkg', '.shp', '.gdb', '.zip']
        
        if not any(filename.lower().endswith(ext) for ext in valid_extensions):
            raise HTTPException(
                status_code=400,
                detail=f"Formato file non supportato. Formati validi: {', '.join(valid_extensions)}"
            )
        
        # Parse selected_columns da JSON string
        columns_list = None
        if selected_columns:
            import json
            try:
                columns_list = json.loads(selected_columns)
            except json.JSONDecodeError:
                logger.warning(f"Errore parsing selected_columns: {selected_columns}")
        
        # Crea file source
        file_source = file_source_service.create_from_upload(
            file_content=file.file,
            filename=filename,
            name=name,
            description=description,
            layer=layer,
            sheet=sheet,
            selected_columns=columns_list
        )
        
        return file_source
    
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore upload file: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore durante l'upload: {str(e)}")


@router.get("", response_model=FileSourceList)
def list_files():
    """Lista tutti i file source disponibili."""
    return file_source_service.list_file_sources()


@router.get("/{file_id}", response_model=FileSource)
def get_file(file_id: str):
    """Ottieni dettagli di un file source."""
    file_source = file_source_service.get_file_source(file_id)
    
    if not file_source:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    
    return file_source


@router.put("/{file_id}", response_model=FileSource)
def update_file(file_id: str, data: FileSourceUpdate):
    """
    Aggiorna un file source.
    
    Puoi aggiornare:
    - name: Nome descrittivo
    - description: Descrizione
    - layer: Layer da usare (per GDB/GPKG)
    - sheet: Sheet da usare (per Excel)
    
    Se cambi layer/sheet, le colonne vengono ricalcolate automaticamente.
    """
    file_source = file_source_service.update_file_source(file_id, data)
    
    if not file_source:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    
    return file_source


@router.delete("/{file_id}")
def delete_file(file_id: str):
    """Elimina un file source e tutti i suoi file."""
    success = file_source_service.delete_file_source(file_id)
    
    if not success:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    
    return {"status": "deleted", "file_id": file_id}


@router.get("/{file_id}/layers", response_model=FileSourceLayersResponse)
def get_file_layers(file_id: str):
    """
    Ottieni lista layer/sheet disponibili in un file.
    
    - Per GDB/GPKG: restituisce lista layer
    - Per XLSX: restituisce lista sheet
    - Per altri formati: liste vuote
    
    Utile per mostrare un dropdown di selezione layer/sheet.
    """
    try:
        return file_source_service.get_layers_info(file_id)
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore listing layers: {e}")
        raise HTTPException(status_code=500, detail=f"Errore: {str(e)}")


@router.get("/{file_id}/preview", response_model=FileSourcePreview)
def preview_file(file_id: str, limit: int = Query(100, ge=1, le=1000)):
    """
    Ottieni preview dei dati del file.
    
    Parametri:
    - limit: Numero massimo righe (default 100, max 1000)
    
    Restituisce:
    - columns: Lista colonne con nome e tipo
    - rows: Prime N righe come lista di dict
    - total_count: Numero totale righe nel file
    - preview_count: Numero righe nella preview
    """
    try:
        return file_source_service.get_preview(file_id, limit)
    
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    
    except Exception as e:
        logger.error(f"Errore preview file: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Errore: {str(e)}")


@router.get("/{file_id}/count")
def get_file_count(file_id: str):
    """Ottieni conteggio righe del file."""
    file_source = file_source_service.get_file_source(file_id)
    
    if not file_source:
        raise HTTPException(status_code=404, detail=f"File source '{file_id}' non trovato")
    
    return {
        "file_id": file_id,
        "row_count": file_source.row_count
    }