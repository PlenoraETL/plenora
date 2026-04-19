"""
Modelli Pydantic per il modulo ESEGUIBILI.
"""

from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field


class ExpectedColumn(BaseModel):
    """Una colonna che la catena si aspetta nel file di input."""
    name: str                              # nome colonna atteso (es. "cod_comune")
    type: Optional[str] = "str"            # tipo inferito (str, int, float, ecc.)
    required: bool = True                  # obbligatoria per l'esecuzione?
    used_by: Optional[str] = None          # id del nodo che la utilizza (debug/info)
    description: Optional[str] = None      # opzionale: descrizione per UI


class EseguibileCreate(BaseModel):
    """Payload per creare un nuovo eseguibile da un progetto arricchisci esistente."""
    source_project_id: str                 # id del progetto arricchisci da clonare
    name: str                              # nome pubblico dell'eseguibile
    description: Optional[str] = ""
    default_output_format: str = "xlsx"    # xlsx, csv, json, geojson, ecc.
    default_output_name: Optional[str] = None  # se None → "{name}_{timestamp}"
    icon: Optional[str] = None             # emoji/carattere mostrato sulla card (fallback: ▶)


class EseguibileUpdate(BaseModel):
    """Payload per aggiornare metadata (non il canvas)."""
    name: Optional[str] = None
    description: Optional[str] = None
    default_output_format: Optional[str] = None
    default_output_name: Optional[str] = None
    icon: Optional[str] = None


class EseguibileSummary(BaseModel):
    """Scheda compatta per la lista."""
    id: str
    name: str
    description: Optional[str] = ""
    default_output_format: str
    icon: Optional[str] = None
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    expected_columns_count: int = 0
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente


class EseguibileDetails(BaseModel):
    """Dettaglio completo (usato dal wizard)."""
    id: str
    name: str
    description: Optional[str] = ""
    expected_columns: List[ExpectedColumn]
    default_output_format: str
    default_output_name: Optional[str] = None
    icon: Optional[str] = None
    source_canvas_id: Optional[str] = None
    group_id: str
    created_by: str
    created_at: str
    updated_at: str
    can_edit: Optional[bool] = None  # calcolato per l'utente corrente
    # Flusso file-based (rest_file come ultimo nodo api_enrich):
    # il wizard di esecuzione deve disabilitare il selettore di output_format
    # perche' l'estensione e' determinata dal connettore, non dall'utente.
    output_is_file_based: bool = False
    inferred_output_extension: Optional[str] = None  # senza punto, es. "zip"


class ColumnMappingEntry(BaseModel):
    """Una riga del mapping: colonna catena → colonna file utente."""
    chain_column: str                      # nome atteso dalla catena
    file_column: str                       # nome presente nel file caricato


class ExecuteRequest(BaseModel):
    """Payload per eseguire un eseguibile con un file utente."""
    file_id: str                           # id del FileSource temp creato al momento dell'upload
    column_mapping: List[ColumnMappingEntry]
    output_format: Optional[str] = None    # override del default
    output_name: Optional[str] = None      # override del default


class AnalyzeUploadResponse(BaseModel):
    """Risposta all'upload di un file per analisi."""
    file_id: str                           # id del FileSource temp
    detected_columns: List[Dict[str, Any]] # [{name, type, sample_values}]
    row_count: int
    auto_mapping: Dict[str, str] = {}      # {chain_col: file_col} — suggerimento euristico
    preview_rows: List[Dict[str, Any]] = []  # prime 10 righe
