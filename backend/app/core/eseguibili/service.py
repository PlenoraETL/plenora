"""
Business logic per gli eseguibili.

Operazioni principali:
- create_from_project(): clona il canvas di un progetto arricchisci e crea un eseguibile
- analyze_file_upload(): carica file utente, estrae colonne, suggerisce mapping
- execute(): sostituisce la sorgente nel canvas clonato e lancia il task di enrichment
"""

import copy
import json
import uuid
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from app.utils.logger import get_logger

from . import db as eseguibili_db
from .scanner import extract_expected_columns

logger = get_logger(__name__)


# CREATE — clona canvas da progetto arricchisci esistente

def create_from_project(
    source_project_id: str,
    name: str,
    group_id: str,
    created_by: str,
    description: str = "",
    default_output_format: str = "xlsx",
    default_output_name: Optional[str] = None,
    icon: Optional[str] = None,
) -> Dict[str, Any]:
    """Crea un eseguibile clonando il canvas di un progetto arricchisci.

    Raises:
        ValueError: se il progetto non esiste o non è di tipo arricchisci,
                    o se l'utente non ha accesso al gruppo.
    """
    from app.db.config.projects import get_project

    proj = get_project(source_project_id)
    if not proj:
        raise ValueError(f"Progetto '{source_project_id}' non trovato")

    project_type = proj.get("project_type") or proj.get("type")
    if project_type != "arricchisci":
        raise ValueError(
            f"Solo i progetti di tipo 'arricchisci' possono essere pubblicati come eseguibili "
            f"(trovato: {project_type})"
        )

    # Verifica group_id — l'utente deve appartenere al gruppo del progetto
    project_group = proj.get("group_id")
    if project_group and project_group != group_id:
        raise ValueError(
            "Non puoi pubblicare come eseguibile un progetto di un altro gruppo"
        )

    # Clona canvas
    canvas = proj.get("canvas")
    if isinstance(canvas, str):
        canvas = json.loads(canvas)
    if not canvas or not canvas.get("nodes"):
        raise ValueError("Il progetto non ha un canvas valido")

    cloned_canvas = copy.deepcopy(canvas)

    # Scanner: estrai colonne attese
    expected_columns = extract_expected_columns(cloned_canvas)

    # Verifica che ci sia almeno una sorgente sostituibile
    # (file_source, project_source, staticValues — tutti "input entry point" validi)
    source_nodes = [
        n for n in cloned_canvas.get("nodes", [])
        if n.get("type") in ("file_source", "project_source", "staticValues")
    ]
    if not source_nodes:
        raise ValueError(
            "Il progetto non ha nodi sorgente (file_source, project_source o staticValues) "
            "sostituibili a runtime."
        )
    if len(source_nodes) > 1:
        raise ValueError(
            f"Phase 1: sono supportate solo catene con 1 sola sorgente. "
            f"Trovate {len(source_nodes)} sorgenti. Riduci a 1 per pubblicare come eseguibile."
        )

    record = eseguibili_db.create_eseguibile(
        name=name,
        canvas=cloned_canvas,
        expected_columns=expected_columns,
        group_id=group_id,
        created_by=created_by,
        description=description,
        default_output_format=default_output_format,
        default_output_name=default_output_name,
        source_canvas_id=source_project_id,
        icon=icon,
    )
    logger.info(
        f"Eseguibile creato: {record['id']} ({name}) da progetto {source_project_id}",
        op="eseguibili_create"
    )
    return record


# ANALYZE UPLOAD — upload file utente + estrazione colonne

def analyze_file_upload(
    eseguibile: Dict[str, Any],
    file_path: str,
    filename: str,
) -> Dict[str, Any]:
    """Carica un file temporaneo e ritorna colonne rilevate + auto-mapping.

    Non crea ancora il FileSource definitivo nel DB; il file è su disco temp.
    Il file_id ritornato identifica il file temp per la successiva execute().

    Args:
        eseguibile: record eseguibile (per expected_columns)
        file_path: path del file temp su disco (già scritto)
        filename: nome originale del file

    Returns:
        dict con file_id, detected_columns, row_count, auto_mapping, preview_rows
    """
    from app.utils.dataframe import load_dataframe_from_file

    df = load_dataframe_from_file(file_path)
    if df is None or df.empty:
        raise ValueError("Il file caricato è vuoto o non è stato possibile leggerlo")

    # Detect colonne
    detected = []
    for col in df.columns:
        dtype_str = str(df[col].dtype)
        if dtype_str.startswith("int"):
            col_type = "int"
        elif dtype_str.startswith("float"):
            col_type = "float"
        elif dtype_str.startswith("datetime"):
            col_type = "datetime"
        elif dtype_str == "bool":
            col_type = "bool"
        else:
            col_type = "str"
        sample_values = df[col].dropna().head(3).astype(str).tolist()
        detected.append({
            "name": col,
            "type": col_type,
            "sample_values": sample_values,
        })

    # Auto-mapping euristico: match case-insensitive
    expected_columns = eseguibile.get("expected_columns") or []
    expected_names = [c.get("name") for c in expected_columns if c.get("name")]
    file_cols_lower = {c.lower(): c for c in df.columns}

    auto_mapping: Dict[str, str] = {}
    for exp_name in expected_names:
        if not exp_name:
            continue
        # 1. Match esatto (case-sensitive)
        if exp_name in df.columns:
            auto_mapping[exp_name] = exp_name
            continue
        # 2. Match case-insensitive
        if exp_name.lower() in file_cols_lower:
            auto_mapping[exp_name] = file_cols_lower[exp_name.lower()]
            continue
        # 3. Match con underscore/spazio normalizzati
        norm = exp_name.lower().replace("_", "").replace(" ", "")
        for fc_lower, fc_original in file_cols_lower.items():
            if fc_lower.replace("_", "").replace(" ", "") == norm:
                auto_mapping[exp_name] = fc_original
                break

    # Preview prime 10 righe (serializzabili)
    preview_df = df.head(10)
    # Converti a dict JSON-safe: NaN → None
    import math
    preview_rows = []
    for row in preview_df.to_dict(orient="records"):
        clean = {}
        for k, v in row.items():
            if isinstance(v, float) and math.isnan(v):
                clean[k] = None
            else:
                clean[k] = v
        preview_rows.append(clean)

    file_id = f"esec_upload_{uuid.uuid4().hex[:12]}"

    return {
        "file_id": file_id,
        "file_path": file_path,  # interno, non esposto al frontend
        "filename": filename,
        "detected_columns": detected,
        "row_count": int(len(df)),
        "auto_mapping": auto_mapping,
        "preview_rows": preview_rows,
    }


# EXECUTE — sostituisce sorgente nel canvas e lancia task

def execute(
    eseguibile: Dict[str, Any],
    file_path: str,
    column_mapping: List[Dict[str, str]],
    output_format: Optional[str] = None,
    output_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Esegue un eseguibile: rinomina colonne, crea FileSource temp, sostituisce
    il nodo sorgente nel canvas clonato, lancia execute_enrichment_task.

    Args:
        eseguibile: record eseguibile
        file_path: path del file caricato (dal step analyze)
        column_mapping: [{chain_column, file_column}] — come l'utente ha mappato
        output_format: override del default
        output_name: override del default

    Returns:
        dict con task_id, project_id (id dell'eseguibile), async flag
    """
    import io
    from app.utils.dataframe import load_dataframe_from_file
    from app.services.file_service import file_source_service
    from app.services.task_service import task_service
    from app.config import settings

    # 1. Carica DataFrame originale
    df = load_dataframe_from_file(file_path)
    if df is None or df.empty:
        raise ValueError("File sorgente vuoto o illeggibile")

    # 2. Rinomina colonne secondo il mapping (solo quelle presenti)
    # column_mapping è [{chain_column, file_column}]: inverto in {file_col: chain_col}
    rename_map: Dict[str, str] = {}
    for m in column_mapping:
        chain_col = m.get("chain_column")
        file_col = m.get("file_column")
        if chain_col and file_col and file_col in df.columns:
            rename_map[file_col] = chain_col

    if rename_map:
        df = df.rename(columns=rename_map)
        logger.info(f"Rename colonne: {rename_map}", op="eseguibili_execute")

    # 3. Verifica che tutte le colonne obbligatorie siano ora presenti
    expected_columns = eseguibile.get("expected_columns") or []
    missing_required = [
        c["name"] for c in expected_columns
        if c.get("required") and c["name"] not in df.columns
    ]
    if missing_required:
        raise ValueError(
            f"Colonne obbligatorie non mappate: {', '.join(missing_required)}"
        )

    # 4. Salva DataFrame rinominato come FileSource temporaneo nel DB
    # Riusiamo il sistema file_source esistente — marchiamo con un nome speciale
    temp_name = f"__exec_temp_{eseguibile['id']}_{uuid.uuid4().hex[:8]}"
    temp_filename = f"{temp_name}.csv"

    # Serializziamo il DataFrame in memoria come CSV
    csv_buffer = io.BytesIO()
    df.to_csv(csv_buffer, index=False, encoding="utf-8")
    csv_buffer.seek(0)

    # Creiamo un FileSource nel DB usando il service esistente
    file_source = file_source_service.create_from_upload(
        file_content=csv_buffer,
        filename=temp_filename,
        name=temp_name,
        description=f"File temporaneo per eseguibile {eseguibile['id']}",
        group_id=eseguibile["group_id"],
    )
    # create_from_upload ritorna un oggetto FileSource (pydantic) — accediamo a .id
    temp_file_source_id = file_source.id if hasattr(file_source, "id") else file_source["id"]
    logger.info(
        f"FileSource temporaneo creato: {temp_file_source_id} "
        f"({len(df)} righe, {len(df.columns)} colonne)",
        op="eseguibili_execute"
    )

    # 5. Clona canvas e sostituisci il nodo sorgente
    canvas = copy.deepcopy(eseguibile["canvas"])
    nodes = canvas.get("nodes", [])

    source_node = None
    for n in nodes:
        if n.get("type") in ("file_source", "project_source", "staticValues"):
            source_node = n
            break

    if not source_node:
        raise ValueError("Canvas dell'eseguibile non contiene un nodo sorgente")

    # Sostituisci con il file_source temporaneo
    original_type = source_node.get("type")
    source_node["type"] = "file_source"
    source_node["file_source_id"] = temp_file_source_id
    # Azzera campi delle altre tipologie (project_source, staticValues)
    source_node.pop("source_project_id", None)
    source_node.pop("fields", None)  # staticValues
    # Aggiorna anche in data{}
    if "data" not in source_node or source_node["data"] is None:
        source_node["data"] = {}
    source_node["data"]["file_source_id"] = temp_file_source_id
    source_node["data"].pop("source_project_id", None)
    source_node["data"].pop("fields", None)  # staticValues

    logger.info(
        f"Sostituzione sorgente nel canvas: {original_type} -> file_source {temp_file_source_id}",
        op="eseguibili_execute"
    )

    # 6. Lancia task enrichment
    final_format = output_format or eseguibile.get("default_output_format") or "xlsx"

    # Determina il nome file finale (priorità: utente > default eseguibile > nome eseguibile)
    # Strippa eventuale estensione: il task la riaggiunge con `final_format`.
    raw_name = (
        (output_name or "").strip()
        or (eseguibile.get("default_output_name") or "").strip()
        or eseguibile.get("name", "risultato")
    )
    # Sanitizza: rimuovi estensione, caratteri pericolosi per filesystem
    import re
    base_name = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", raw_name)  # togli .xlsx, .csv, etc.
    base_name = re.sub(r'[\\/:*?"<>|]', "_", base_name).strip() or "risultato"

    # Applica al result node (sempre, non solo se l'utente ha specificato qualcosa)
    for n in nodes:
        if n.get("type") == "result":
            if "data" not in n or n["data"] is None:
                n["data"] = {}
            n["data"]["fileName"] = base_name
            n["data"]["format"] = final_format
            # Setta anche i campi flat per compatibilità con il merge {flat, **data}
            n["fileName"] = base_name
            n["format"] = final_format
            break

    logger.info(
        f"Eseguibile {eseguibile['id']}: output filename='{base_name}.{final_format}'",
        op="eseguibili_execute"
    )

    task_info = task_service.create_enrichment_task(
        project_id=eseguibile["id"],   # usiamo l'id dell'eseguibile come "project_id" per tracking
        canvas=canvas,
        output_format=final_format,
        force_recalculate=True,        # sempre True: ogni esecuzione è una nuova run
        # Cleanup automatico al termine del task: rimuove il FileSource temporaneo
        # creato sopra (sia in caso di successo sia in caso di errore).
        cleanup_file_source_ids=[temp_file_source_id],
        is_eseguibile=True,
    )

    logger.info(
        f"Task eseguibile avviato: task_id={task_info.task_id} eseguibile={eseguibile['id']}",
        op="eseguibili_execute"
    )

    return {
        "async": True,
        "task_id": task_info.task_id,
        "project_id": eseguibile["id"],
        "temp_file_source_id": temp_file_source_id,
        "message": "Esecuzione avviata. Usa GET /api/tasks/{task_id} per il progresso.",
    }
