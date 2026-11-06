"""
Modelli Pydantic per il modulo Files.

Gestione file sorgente (CSV, Excel, GeoJSON, GeoPackage, Shapefile, FileGDB).
"""

from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from app.shared.execution.column_info import ColumnInfo


class FileSource(BaseModel):
    """Configurazione di un file esterno usato come sorgente dati."""
    id: str
    name: str
    description: str = ""
    original_filename: str
    # Formati supportati dal parser: csv, xlsx, json, geojson, gpkg, shp, gdb, zip
    # Formati raw (blob senza parsing, vedi create_raw_from_upload): dxf
    # unknown, bin, e qualsiasi altra estensione binaria. Per questo usiamo
    # `str` libero invece di `Literal` — la classe di formati evolve (es
    # upload DXF per eseguibili workflow) e ristringere qui crasha la
    # validazione sul raw fallback
    format: str
    group_id: str = ""
    layer: Optional[str] = None
    sheet: Optional[str] = None
    columns: List[ColumnInfo] = []
    row_count: int = 0
    file_size: int = 0
    crs: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente


class FileSourceCreate(BaseModel):
    """Dati per creare un file sorgente (dopo l'upload del file)."""
    name: str
    description: str = ""
    layer: Optional[str] = None
    sheet: Optional[str] = None


class FileSourceUpdate(BaseModel):
    """Dati parziali per aggiornare un file sorgente."""
    name: Optional[str] = None
    description: Optional[str] = None
    layer: Optional[str] = None
    sheet: Optional[str] = None


class FileSourceList(BaseModel):
    """Wrapper per la lista di file sorgente."""
    files: List[FileSource]


# Nomi di colonna mostrati nella card della lista (FileManager/FileCard).
FILE_LIST_COLUMN_PREVIEW = 5


class FileSourceSummary(BaseModel):
    """Un file nella lista (GET /files, UI-1 06/11/2026, audit C26).

    Senza `columns`: la lista e' letta da dashboard, gestore file ed editor,
    che usano solo quante sono e i primi nomi. Le colonne intere sono nel
    dettaglio (GET /files/{id}).
    """
    id: str
    name: str
    description: str = ""
    original_filename: str
    format: str
    group_id: str = ""
    layer: Optional[str] = None
    sheet: Optional[str] = None
    column_count: int = 0
    column_names_preview: List[str] = []
    row_count: int = 0
    file_size: int = 0
    crs: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    can_edit: Optional[bool] = None

    @classmethod
    def from_file_source(cls, source: "FileSource") -> "FileSourceSummary":
        data = source.model_dump(exclude={"columns"})
        columns = source.columns or []
        return cls(
            **data,
            column_count=len(columns),
            column_names_preview=[c.name for c in columns[:FILE_LIST_COLUMN_PREVIEW]],
        )


class FileSourceSummaryList(BaseModel):
    """Una pagina della lista dei file (limit/offset): `files` senza colonne."""
    files: List[FileSourceSummary]


class FileSourceLayersResponse(BaseModel):
    """Risposta con i layer o fogli disponibili in un file multi-layer/multi-sheet."""
    file_id: str
    format: str
    layers: List[str] = []
    sheets: List[str] = []


class FileSourcePreview(BaseModel):
    """Anteprima dei dati contenuti in un file sorgente."""
    file_id: str
    columns: List[ColumnInfo]
    rows: List[dict]
    total_count: int
    preview_count: int
