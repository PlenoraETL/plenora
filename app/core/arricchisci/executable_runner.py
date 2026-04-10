"""
Runner per eseguibili locali nel modulo ARRICCHISCI.

Architettura
============
Questo modulo gestisce l'esecuzione di processori locali (script Python,
moduli, binari) come alternativa alle chiamate API HTTP. Viene invocato
da api_connector.py quando il nodo api_enrich ha connection_type='executable'.

A differenza degli enricher/generator che lavorano riga-per-riga su DataFrame,
gli executable lavorano file→file: ricevono un file di input e producono
un file di output.

Flusso:
1. Risolve il processore (built-in o custom)
2. Localizza il file di input (da file_source o upload)
3. Esegue il modulo Python o script esterno
4. Restituisce ExecutableResult con path output + metadata

Processori built-in:
- dxf_imdf: Converte DXF stazioni RFI → archivio IMDF GeoJSON (ZIP)

Tipi di esecuzione:
- python_module: importa ed esegue una funzione Python
- script: esegue uno script Python via subprocess
- builtin: alias per un processore registrato in BUILTIN_REGISTRY

Integrazione canvas ARRICCHISCI:
    [file_source: stazione.zip] → [executable: DXF→IMDF] → [result: imdf.zip]
"""

import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime
from dataclasses import dataclass, field, asdict

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# REGISTRY PROCESSORI BUILT-IN
# =============================================================================

BUILTIN_REGISTRY: Dict[str, Dict] = {
    "dxf_imdf": {
        "module": "dxf_imdf_sync.process",
        "function": "process",
        "name": "DXF → IMDF",
        "description": "Converte DXF di stazioni RFI in archivio IMDF (GeoJSON). "
                       "Accetta: singolo DXF, cartella, o ZIP con DXF + Excel opzionale.",
        "category": "gis",
        "icon": "🏗️",
        "input_type": "file",
        "output_type": "file",
        "accepted_extensions": [".zip", ".dxf"],
        "output_extension": ".zip",
        "parameters": [
            {
                "name": "site_id",
                "label": "SITE_ID",
                "type": "string",
                "required": False,
                "description": "Filtro stazione per dati Excel (auto-detect se omesso)"
            },
            {
                "name": "excel_path",
                "label": "File Excel DB GIS",
                "type": "file",
                "required": False,
                "description": "File Excel per enrichment (auto-detect in ZIP/cartella)"
            }
        ]
    }
}


# =============================================================================
# MODELLI RISULTATO
# =============================================================================

@dataclass
class ExecutableResult:
    """Risultato di un'esecuzione locale."""
    success: bool
    output_path: str = ""
    output_filename: str = ""
    output_size_bytes: int = 0
    execution_time_ms: float = 0
    metadata: Dict = field(default_factory=dict)
    error: str = ""
    is_file_result: bool = True  # Flag per distinguere da DataFrame

    def to_dict(self) -> Dict:
        return asdict(self)


# =============================================================================
# FUNZIONI PUBBLICHE
# =============================================================================

def list_builtin_executables() -> List[Dict]:
    """Lista processori built-in disponibili."""
    result = []
    for exec_id, config in BUILTIN_REGISTRY.items():
        result.append({
            "id": exec_id,
            "name": config["name"],
            "description": config["description"],
            "category": config.get("category", "custom"),
            "icon": config.get("icon", "⚙️"),
            "input_type": config.get("input_type", "file"),
            "output_type": config.get("output_type", "file"),
            "accepted_extensions": config.get("accepted_extensions", []),
            "parameters": config.get("parameters", [])
        })
    return result


def run_executable(api_config: Dict, input_path: str,
                   output_dir: str, params: Dict = None) -> ExecutableResult:
    """
    Esegue un processore locale.

    Args:
        api_config: configurazione completa del connettore (da config.json)
        input_path: percorso file input
        output_dir: directory dove salvare l'output
        params: parametri aggiuntivi (es. site_id)

    Returns:
        ExecutableResult
    """
    import time
    start = time.time()

    exec_config = api_config.get("executable", {})
    exec_type = exec_config.get("type", "python_module")

    try:
        # Risolvi built-in
        if exec_type == "builtin":
            builtin_id = exec_config.get("builtin_id")
            builtin = BUILTIN_REGISTRY.get(builtin_id)
            if not builtin:
                return ExecutableResult(
                    success=False,
                    error=f"Processore built-in '{builtin_id}' non trovato. "
                          f"Disponibili: {list(BUILTIN_REGISTRY.keys())}"
                )
            exec_config = {**builtin, **exec_config}  # merge, exec_config override
            exec_type = "python_module"

        if exec_type == "python_module":
            result = _run_python_module(exec_config, input_path, output_dir, params or {})
        elif exec_type == "script":
            result = _run_script(exec_config, input_path, output_dir, params or {})
        else:
            return ExecutableResult(
                success=False,
                error=f"Tipo executable non supportato: {exec_type}"
            )

        result.execution_time_ms = (time.time() - start) * 1000
        return result

    except Exception as e:
        logger.error(f"[CRASH] Errore esecuzione executable: {e}", exc_info=True)
        return ExecutableResult(
            success=False,
            error=str(e),
            execution_time_ms=(time.time() - start) * 1000
        )


def resolve_input_file(node_data: Dict, upstream_nodes: Dict,
                       edges: List[Dict], node_id: str) -> Optional[str]:
    """
    Risolve il percorso del file di input per un nodo executable.

    Cerca l'input tra:
    1. node_data.exec_params.input_file (path esplicito)
    2. Nodo file_source collegato → percorso file originale
    3. Nodo project_source → ultimo output file

    Returns:
        Percorso assoluto al file di input, o None
    """
    # 1. Path esplicito nei parametri
    exec_params = node_data.get("exec_params", {})
    explicit_path = exec_params.get("input_file")
    if explicit_path and Path(explicit_path).exists():
        return explicit_path

    # 2. Cerca nodo sorgente collegato via edge
    input_edge = next((e for e in edges if e['target'] == node_id), None)
    if not input_edge:
        return None

    source_id = input_edge['source']

    # Trova il nodo sorgente nella lista nodes del canvas
    # Il caller deve passare i nodi completi
    source_node_data = upstream_nodes.get(source_id, {})
    source_type = source_node_data.get("_node_type", "")

    if source_type == "file_source":
        return _resolve_file_source_path(source_node_data)
    elif source_type == "project_source":
        return _resolve_project_output_path(source_node_data)

    return None


# =============================================================================
# ESECUZIONE PYTHON MODULE
# =============================================================================

def _run_python_module(exec_config: Dict, input_path: str,
                       output_dir: str, params: Dict) -> ExecutableResult:
    """Esegue un modulo Python importandolo direttamente."""
    module_name = exec_config.get("module")
    function_name = exec_config.get("function", "process")

    if not module_name:
        return ExecutableResult(success=False, error="'module' non specificato")

    logger.info(f"Esecuzione modulo: {module_name}.{function_name}")
    logger.info(f"  Input: {input_path}")

    # Import dinamico
    try:
        module = importlib.import_module(module_name)
        func = getattr(module, function_name)
    except ImportError as e:
        return ExecutableResult(
            success=False,
            error=f"Modulo '{module_name}' non installato. "
                  f"Installa con: pip install {module_name.split('.')[0]}\n"
                  f"Dettaglio: {e}"
        )
    except AttributeError:
        return ExecutableResult(
            success=False,
            error=f"Funzione '{function_name}' non trovata in '{module_name}'"
        )

    # Prepara output path
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    input_stem = Path(input_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_ext = exec_config.get("output_extension", ".zip")
    output_filename = f"{input_stem}_output_{timestamp}{output_ext}"
    output_path = str(Path(output_dir) / output_filename)

    # Costruisci kwargs per la funzione
    func_kwargs = {}
    if params.get("site_id"):
        func_kwargs["site_id"] = params["site_id"]
    if params.get("excel_path"):
        func_kwargs["excel_path"] = params["excel_path"]

    # Esegui
    logger.info(f"  Chiamata: {function_name}({input_path}, output_zip={output_path}, "
                f"{func_kwargs})")

    result_path = func(input_path, output_zip=output_path, **func_kwargs)

    # Verifica output
    result_file = Path(result_path)
    if not result_file.exists():
        return ExecutableResult(
            success=False,
            error=f"L'esecuzione non ha prodotto output: {result_path}"
        )

    output_size = result_file.stat().st_size

    logger.info(f"  ✓ Output: {result_path} ({output_size / 1024:.0f} KB)")

    return ExecutableResult(
        success=True,
        output_path=str(result_path),
        output_filename=result_file.name,
        output_size_bytes=output_size,
        metadata={
            "module": module_name,
            "function": function_name,
            "input": input_path,
            "params": params
        }
    )


# =============================================================================
# ESECUZIONE SCRIPT ESTERNO
# =============================================================================

def _run_script(exec_config: Dict, input_path: str,
                output_dir: str, params: Dict) -> ExecutableResult:
    """Esegue uno script Python via subprocess."""
    script_path = exec_config.get("script_path")

    if not script_path or not Path(script_path).exists():
        return ExecutableResult(
            success=False,
            error=f"Script non trovato: {script_path}"
        )

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    input_stem = Path(input_path).stem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_ext = exec_config.get("output_extension", ".zip")
    output_filename = f"{input_stem}_output_{timestamp}{output_ext}"
    output_path = str(Path(output_dir) / output_filename)

    # Costruisci comando
    cmd = [sys.executable, script_path, input_path, "-o", output_path]
    for key, value in params.items():
        if value:
            cmd.extend([f"--{key}", str(value)])

    logger.info(f"Esecuzione script: {' '.join(cmd)}")

    result = subprocess.run(
        cmd, capture_output=True, text=True, timeout=600  # 10 min max
    )

    if result.returncode != 0:
        return ExecutableResult(
            success=False,
            error=f"Script uscito con codice {result.returncode}:\n"
                  f"{result.stderr[-500:] if result.stderr else 'nessun errore'}"
        )

    result_file = Path(output_path)
    if not result_file.exists():
        return ExecutableResult(
            success=False,
            error=f"Script completato ma output non trovato: {output_path}\n"
                  f"stdout: {result.stdout[-300:] if result.stdout else ''}"
        )

    return ExecutableResult(
        success=True,
        output_path=output_path,
        output_filename=result_file.name,
        output_size_bytes=result_file.stat().st_size,
        metadata={
            "script": script_path,
            "params": params,
            "stdout": result.stdout[-500:] if result.stdout else ""
        }
    )


# =============================================================================
# HELPER: RISOLUZIONE PATH FILE
# =============================================================================

def _resolve_file_source_path(node_data: Dict) -> Optional[str]:
    """Risolve il percorso originale di un file_source."""
    file_source_id = node_data.get("file_source_id")
    if not file_source_id:
        return None

    try:
        from app.services.file_service import file_source_service
        file_info = file_source_service.get_file_source(file_source_id)
        if file_info:
            # Il file originale è nella directory uploads
            file_path = file_info.get("file_path") or file_info.get("path")
            if file_path and Path(file_path).exists():
                return str(file_path)

            # Fallback: cerca nella directory del file source
            fs_dir = settings.FILE_SOURCES_DIR / file_source_id
            if fs_dir.exists():
                for f in fs_dir.iterdir():
                    if f.is_file() and not f.name.startswith('.'):
                        return str(f)
    except Exception as e:
        logger.warning(f"Errore risoluzione file_source {file_source_id}: {e}")

    return None


def _resolve_project_output_path(node_data: Dict) -> Optional[str]:
    """Risolve l'ultimo file di output di un progetto dal blob DB."""
    project_id = node_data.get("source_project_id")
    if not project_id:
        return None

    try:
        from app.utils.result_store import get_result_as_tempfile
        tmp = get_result_as_tempfile(project_id)
        return str(tmp) if tmp else None
    except Exception:
        return None
