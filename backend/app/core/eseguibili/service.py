"""
Business logic per gli eseguibili.

Operazioni principali:
"""

import io
import re

import copy
import hashlib
import json
import uuid
from typing import BinaryIO, Callable, Mapping, Optional, Sequence, Union

from app.utils.logger import get_logger
from app.core.dataframes import frame_io as dataframe_frame_io
from app.core.dataframes import tabular as dataframe_tabular
from app.core.dataframes.export_writer import normalize_export_format
from app.core.projects import access_context as project_access_context
from app.core.projects.canvas_models import CanvasState
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.files import db as files_db
from app.core.files.service import file_source_service
from app.core.connections import connection_sets
from app.shared.contracts.payloads import JsonObject

from . import db as eseguibili_db
from .models import (
    AnalyzeFileUploadResult,
    DetectedUploadColumn,
    ExecutableInputBinding,
    ExecutableManifest,
    ExecutableRunResult,
    EseguibileExecutionResult,
    ConnectionInputOption,
)
from .runtime import resolve_runtime_inputs
from app.shared.execution.runtime_identity import RuntimeIdentity
from app.shared.execution.runtime_bound_fields import runtime_bound_context
from .manifest import binding_problems, compile_manifest, compile_project_inputs
from .storage import INTERNAL_ARTIFACT_PREFIX

logger = get_logger(__name__)

UPLOAD_METADATA_KEY = "eseguibili_upload"


def binding_warnings(raw_bindings: object, canvas: Mapping[str, object]) -> list[JsonObject]:
    """``binding_warnings`` di un Eseguibile pubblicato: i binding del manifest
    attivo verificati contro le capability correnti con la STESSA regola
    della pubblicazione (``manifest.check_binding``) sul canvas della
    versione (quello che l'esecuzione usa). Un binding non leggibile come
    tale non produce avvisi (il manifest lo rifiuta gia' all'esecuzione)."""
    if not isinstance(raw_bindings, list):
        return []
    bindings: list[ExecutableInputBinding] = []
    for raw in raw_bindings:
        try:
            bindings.append(ExecutableInputBinding.model_validate(raw))
        except ValueError:
            continue
    return [
        {
            "input_key": problem.input_key,
            "target_node_id": problem.target_node_id,
            "target_field": problem.target_field,
            "motivo": problem.motivo,
        }
        for problem in binding_problems(bindings, canvas)
    ]


def hydrate_manifest_connection_options(
    manifest: ExecutableManifest,
    *,
    group_id: str,
) -> ExecutableManifest:
    """Aggiorna le sole opzioni pubbliche usando lo stato corrente del set."""

    hydrated = []
    for definition in manifest.inputs:
        if definition.kind != "connection" or not definition.connection_set_id:
            hydrated.append(definition)
            continue
        options = connection_sets.connection_set_options(
            definition.connection_set_id,
            expected_group_id=group_id,
        )
        hydrated.append(
            definition.model_copy(
                update={
                    "connection_options": [
                        ConnectionInputOption(key=item.key, label=item.label)
                        for item in options
                    ]
                }
            )
        )
    return manifest.model_copy(update={"inputs": hydrated})


def resolve_runtime_connection_id(
    manifest: ExecutableManifest,
    *,
    raw_inputs: Mapping[str, object],
    group_id: str,
) -> str | None:
    definitions = [
        item for item in manifest.inputs if item.kind == "connection"
    ]
    if not definitions:
        return manifest.default_connection_id
    if len(definitions) > 1:
        raise ValueError("Un progetto può dichiarare un solo input connessione")
    definition = definitions[0]
    raw_value = raw_inputs.get(definition.key, definition.default_value)
    selector_key = str(raw_value or "").strip()
    if not selector_key:
        if definition.required:
            raise ValueError(f"Input obbligatorio mancante: {definition.label}")
        return manifest.default_connection_id
    return connection_sets.resolve_connection(
        str(definition.connection_set_id),
        selector_key,
        expected_group_id=group_id,
    )


def _sanitize_output_base_name(raw_name: str) -> str:
    """Return a safe public filename that cannot collide with internal objects."""

    base_name = re.sub(r"\.[a-zA-Z0-9]{1,5}$", "", raw_name)
    base_name = re.sub(r'[\\/:*?"<>|]', "_", base_name).strip() or "risultato"
    if base_name.startswith(INTERNAL_ARTIFACT_PREFIX):
        base_name = f"result_{base_name}"
    return base_name


def get_eseguibile_run(run_id: str) -> JsonObject | None:
    return eseguibili_db.get_eseguibile_run(run_id)


# CREATE — compila una versione immutabile da qualsiasi tipo progetto

def create_from_project(
    source_project_id: str,
    name: str,
    group_id: str,
    created_by: str,
    description: str = "",
    default_output_format: str = "xlsx",
    default_output_name: Optional[str] = None,
    icon: Optional[str] = None,
) -> eseguibili_db.EseguibileRow:
    """Pubblica un progetto ESTRAI/MANIPOLA/ARRICCHISCI come eseguibile.

    Raises:
        ValueError: se il progetto non esiste, il manifest non è valido o
                    l'utente non ha accesso al gruppo.
    """
    proj = project_access_context.load_project_publish_context(source_project_id)
    if not proj:
        raise ValueError(f"Progetto '{source_project_id}' non trovato")

    project_type = str(proj.project_type or "").lower()
    if project_type not in {"estrai", "manipola", "arricchisci"}:
        raise ValueError(
            f"Tipo progetto non pubblicabile come eseguibile: {project_type!r}"
        )

    # Verifica group_id — l'utente deve appartenere al gruppo del progetto
    project_group = proj.group_id
    if project_group and project_group != group_id:
        raise ValueError(
            "Non puoi pubblicare come eseguibile un progetto di un altro gruppo"
        )

    # Clona canvas
    canvas = proj.canvas
    if isinstance(canvas, str):
        canvas = json.loads(canvas)
    if not canvas or not canvas.get("nodes"):
        raise ValueError("Il progetto non ha un canvas valido")

    cloned_canvas = copy.deepcopy(canvas)

    manifest = compile_manifest(
        source_project_id=source_project_id,
        project_type=project_type,
        canvas=cloned_canvas,
        default_connection_id=getattr(proj, "default_connection_id", None),
    )
    manifest = hydrate_manifest_connection_options(manifest, group_id=group_id)
    first_file_input = next(
        (item for item in manifest.inputs if item.kind == "file"), None
    )
    expected_columns = (
        [column.model_dump() for column in first_file_input.expected_columns]
        if first_file_input
        else []
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
        project_type=project_type,
        manifest=manifest.model_dump(mode="json"),
    )
    logger.info(
        f"Eseguibile creato: {record.id} ({name}) da progetto {source_project_id}",
        op="eseguibili_create"
    )
    return record


def publish_new_version(
    *,
    eseguibile: eseguibili_db.EseguibileRow,
    created_by: str,
) -> eseguibili_db.EseguibileRow:
    """Ripubblica lo snapshot corrente del progetto sorgente."""

    source_project_id = eseguibile.source_canvas_id
    if not source_project_id:
        raise ValueError("L'eseguibile non ha un progetto sorgente ripubblicabile")
    proj = project_access_context.load_project_publish_context(source_project_id)
    if proj is None:
        raise ValueError("Il progetto sorgente non esiste piu'")
    if proj.group_id != eseguibile.group_id:
        raise ValueError("Il progetto sorgente appartiene a un altro gruppo")
    project_type = str(proj.project_type or "").lower()
    if project_type != eseguibile.project_type:
        raise ValueError(
            "Il tipo del progetto sorgente e' cambiato; pubblica un nuovo eseguibile"
        )
    canvas = proj.canvas
    if isinstance(canvas, str):
        canvas = json.loads(canvas)
    if not isinstance(canvas, dict) or not canvas.get("nodes"):
        raise ValueError("Il progetto sorgente non ha un canvas valido")
    cloned_canvas = copy.deepcopy(canvas)
    manifest = compile_manifest(
        source_project_id=source_project_id,
        project_type=project_type,
        canvas=cloned_canvas,
        default_connection_id=getattr(proj, "default_connection_id", None),
    )
    manifest = hydrate_manifest_connection_options(
        manifest,
        group_id=eseguibile.group_id,
    )
    version = eseguibili_db.create_eseguibile_version(
        eseguibile_id=eseguibile.id,
        canvas=cloned_canvas,
        manifest=manifest.model_dump(mode="json"),
        project_type=project_type,
        source_project_id=source_project_id,
        created_by=created_by,
    )
    eseguibili_db.activate_eseguibile_version(eseguibile.id, version.id)
    updated = eseguibili_db.get_eseguibile(eseguibile.id)
    if updated is None:
        raise RuntimeError("Eseguibile non trovato dopo la ripubblicazione")
    return updated


# N1 — l'Eseguibile corrisponde ancora al suo progetto?

def _problem_rows(problems) -> list[JsonObject]:
    return [
        {
            "input_key": problem.input_key,
            "target_node_id": problem.target_node_id,
            "target_field": problem.target_field,
            "motivo": problem.motivo,
        }
        for problem in problems
    ]


def _parsed_canvas(canvas: object) -> object:
    if isinstance(canvas, str):
        try:
            return json.loads(canvas)
        except json.JSONDecodeError:
            return None
    return canvas


# P2 (09/11/2026) — confronto SEMANTICO, regola unica di lista, dettaglio,
# editor e home: due canvas «uguali» se l'esecuzione li vede uguali. Entrambi
# passano dal modello che legge l'esecuzione (``CanvasState``: tipi e valori
# predefiniti normalizzati, degli archi solo i campi del modello); poi si
# tolgono, DICHIARATI, i campi che non cambiano l'esecuzione:
#   - nodi: posizione, dimensioni, selezione, trascinamento, misure e stile
#     di React Flow (``EXECUTION_IGNORED_NODE_KEYS``, anche dentro ``data``),
#     campi transitori dell'interfaccia (chiave che inizia con «_», anche
#     dentro ``data``), le note (tipo ``comment``);
#   - archi: l'id; i collegamenti doppi contano una volta (il caricamento
#     dell'editor li toglie e li risalva da solo);
#   - ordine di nodi, archi e chiavi; numeri interi scritti 1 o 1.0.
# Le colonne delle sorgenti rilette dallo schema NON si tolgono: l'esecuzione
# le legge (alias, visibilita', CRS); una rilettura uguale non cambia nulla,
# una diversa e' un cambiamento vero.
EXECUTION_IGNORED_NODE_KEYS = frozenset({
    "position", "positionAbsolute", "width", "height", "measured", "selected",
    "dragging", "resizing", "zIndex", "style", "className", "sourcePosition", "targetPosition",
})


def _plain(value: object) -> object:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _without_ui(fields: Mapping[str, object]) -> JsonObject:
    return {
        k: v for k, v in fields.items()
        if k not in EXECUTION_IGNORED_NODE_KEYS and not str(k).startswith("_")
    }


def execution_view(canvas: object, *, normalize: bool = True) -> JsonObject | None:
    """Il canvas come lo vede l'esecuzione (regola sopra); None se illeggibile
    o, con ``normalize``, se il modello dell'esecuzione non lo accetta."""
    canvas = _parsed_canvas(canvas)
    if not isinstance(canvas, dict):
        return None
    if normalize:
        try:
            canvas = CanvasState.model_validate(canvas).model_dump(mode="json")
        except (TypeError, ValueError):
            return None
    raw_nodes = canvas.get("nodes")
    raw_edges = canvas.get("edges")
    nodes = []
    for node in raw_nodes if isinstance(raw_nodes, list) else []:
        if not isinstance(node, dict) or node.get("type") == "comment":
            continue
        view = _without_ui(node)
        if isinstance(view.get("data"), dict):
            view["data"] = _without_ui(view["data"])
        nodes.append(_plain(view))
    edges = {
        json.dumps(_plain({k: v for k, v in edge.items() if k != "id"}), sort_keys=True, ensure_ascii=False, default=str)
        for edge in (raw_edges if isinstance(raw_edges, list) else []) if isinstance(edge, dict)
    }
    return {"nodes": sorted(nodes, key=lambda n: str(n.get("id"))), "edges": sorted(edges)}


def source_changed(version_canvas: object, project_canvas: object) -> bool | None:
    """La versione e il progetto differiscono per l'esecuzione? None se uno
    dei due canvas manca o non si legge (non si sa: nessun avviso).

    Simmetrico (conferma D1): se il modello dell'esecuzione accetta entrambi
    si confrontano normalizzati; se ne rifiuta anche uno solo, si confrontano
    ENTRAMBI sul JSON com'e' (mai uno normalizzato contro uno grezzo, che
    darebbe un «cambiato» falso e permanente)."""
    published = execution_view(version_canvas)
    current = execution_view(project_canvas)
    if published is None or current is None:
        published = execution_view(version_canvas, normalize=False)
        current = execution_view(project_canvas, normalize=False)
    if published is None or current is None:
        return None
    return published != current


def _source_changed_or_unknown(version_canvas: object, project_canvas: object) -> bool | None:
    """Una riga che non si confronta non rompe la lista (conferma D2): None."""
    try:
        return source_changed(version_canvas, project_canvas)
    except (TypeError, ValueError, AttributeError, KeyError, RecursionError):
        logger.warning("Confronto progetto/versione non riuscito: nessun avviso per questa riga")
        return None


def source_changed_by_id(eseguibile_ids: Sequence[str]) -> Mapping[str, Optional[bool]]:
    """``source_changed`` per piu' Eseguibili con una sola lettura dei canvas
    (versione attiva e progetto sorgente); chi non ha l'una o l'altro manca."""
    if not eseguibile_ids:
        return {}
    return {
        row.id: _source_changed_or_unknown(row.version_canvas, row.project_canvas)
        for row in eseguibili_db.list_source_canvases(list(eseguibile_ids))
    }


def republish_preview(
    canvas: Mapping[str, object],
    *,
    project_type: str,
    source_project_id: str,
    default_connection_id: str | None,
) -> tuple[list[JsonObject], str | None]:
    """Cosa direbbe la ripubblicazione del canvas attuale, PRIMA di farla:
    TUTTI i binding degli input che non troverebbero il bersaglio (stessa
    regola della pubblicazione, ``check_binding``, che li' si ferma al primo)
    e l'eventuale altro rifiuto di ``compile_manifest``."""
    try:
        _, bindings = compile_project_inputs(canvas, project_type)
    except ValueError as exc:
        return [], str(exc)
    problems = _problem_rows(binding_problems(bindings, canvas))
    if problems:
        return problems, None
    try:
        compile_manifest(
            source_project_id=source_project_id,
            project_type=project_type,
            canvas=canvas,
            default_connection_id=default_connection_id,
        )
    except ValueError as exc:
        return [], str(exc)
    return [], None


def source_status(eseguibile: eseguibili_db.EseguibileRow, *, project: object = None) -> JsonObject:
    """Regola unica N1: la versione attiva e il canvas attuale del progetto
    sorgente sono uguali per l'esecuzione (``source_changed``, P2)?

    ``state``: ``same`` | ``changed`` | ``no_source`` (Eseguibile senza
    progetto sorgente o progetto non piu' esistente) | ``no_version``. Con
    ``changed`` c'e' l'anteprima della ripubblicazione. La versione pubblicata
    non cambia da se' (versione = release): qui lo si dice soltanto.
    ``project``: contesto gia' letto (un solo caricamento del canvas per piu'
    Eseguibili dello stesso progetto)."""
    version = (
        eseguibili_db.get_eseguibile_version(eseguibile.active_version_id, eseguibile_id=eseguibile.id)
        if eseguibile.active_version_id else None
    )
    base: JsonObject = {
        "eseguibile_id": eseguibile.id,
        "name": eseguibile.name,
        "version": version.version_number if version else None,
        "published_at": version.created_at if version else None,
        "state": "same",
        "changed": False,
        "binding_problems": [],
        "publish_error": None,
    }
    if version is None:
        return {**base, "state": "no_version", "changed": None}
    source_id = eseguibile.source_canvas_id
    proj = project if project is not None else (
        project_access_context.load_project_publish_context(source_id) if source_id else None
    )
    canvas = _parsed_canvas(proj.canvas) if proj is not None else None
    if proj is None or not isinstance(canvas, dict):
        return {**base, "state": "no_source", "changed": None}
    if not source_changed(version.canvas, canvas):
        return base
    problems, error = republish_preview(
        canvas,
        project_type=str(proj.project_type or "").lower(),
        source_project_id=str(source_id),
        default_connection_id=getattr(proj, "default_connection_id", None),
    )
    return {**base, "state": "changed", "changed": True, "binding_problems": problems, "publish_error": error}


def source_status_for_project(
    project_id: str, *, can_republish: Callable[[eseguibili_db.EseguibileRow], bool],
) -> list[JsonObject]:
    """Gli Eseguibili pubblicati da ``project_id`` che l'utente puo'
    ripubblicare, con il loro stato N1; il canvas del progetto si legge una volta."""
    refs = eseguibili_db.list_eseguibili_by_source(project_id)
    if not refs:
        return []
    proj = project_access_context.load_project_publish_context(project_id)
    out: list[JsonObject] = []
    for ref in refs:
        record = eseguibili_db.get_eseguibile(ref.id)
        if record is None or not can_republish(record):
            continue
        out.append(source_status(record, project=proj))
    return out


# ANALYZE UPLOAD — upload file utente + estrazione colonne

def analyze_file_upload(
    eseguibile: eseguibili_db.EseguibileRow,
    file_content: Union[bytes, BinaryIO],
    filename: str,
    expected_columns_override: Optional[Sequence[Mapping[str, object]]] = None,
) -> AnalyzeFileUploadResult:
    """Carica un file caricato in bytes e ritorna colonne rilevate + auto-mapping.

    Non crea ancora il FileSource definitivo nel DB; il file resta in
    memoria fino al persist_upload_file_source successivo.
    Il file_id ritornato identifica il file temp per la successiva execute.

    Args:
        eseguibile: record eseguibile (per expected_columns)
        file_content: bytes del file caricato (dal router HTTP)
        filename: nome originale del file (per format detection + display)

    Returns:
        dict con file_id, detected_columns, row_count, auto_mapping, preview_rows
    """
    fmt = filename.rsplit(".", 1)[1].lower() if "." in filename else "bin"
    owns_stream = isinstance(file_content, bytes)
    stream = io.BytesIO(file_content) if owns_stream else file_content
    try:
        from app.core.dataframes.load_dispatch import load_by_format_stream

        df, _ = load_by_format_stream(stream, fmt)
    finally:
        if owns_stream:
            stream.close()
    if df is None or df.empty:
        raise ValueError("Il file caricato è vuoto o non è stato possibile leggerlo")

    # Detect colonne
    detected: list[DetectedUploadColumn] = []
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
        sample_values = [
            str(value)
            for value in dataframe_tabular.non_null_head_values(df[col], 3)
        ]
        detected.append(
            DetectedUploadColumn(
                name=str(col),
                type=col_type,
                sample_values=sample_values,
            )
        )

    # Auto-mapping euristico: match case-insensitive
    expected_columns = (
        list(expected_columns_override)
        if expected_columns_override is not None
        else eseguibile.get("expected_columns") or []
    )
    expected_names = [c.get("name") for c in expected_columns if c.get("name")]
    file_cols_lower = {c.lower(): c for c in df.columns}

    auto_mapping: dict[str, str] = {}
    for exp_name in expected_names:
        if not exp_name:
            continue
        # 1. Match esatto (case-sensitive
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

    # Preview prime 10 righe (serializzabili
    preview_rows = dataframe_tabular.dataframe_preview_records(df, 10)

    file_id = f"esec_upload_{uuid.uuid4().hex[:12]}"

    return AnalyzeFileUploadResult(
        file_id=file_id,
        file_path="",
        filename=filename,
        detected_columns=detected,
        row_count=int(len(df)),
        auto_mapping=auto_mapping,
        preview_rows=preview_rows,
    )


def persist_upload_file_source(
    *,
    eseguibile: eseguibili_db.EseguibileRow,
    file_content: Union[bytes, BinaryIO],
    filename: str,
    user_id: str,
    group_id: str,
    input_key: Optional[str] = None,
) -> str:
    """Persist an analyzed upload behind a cross-host file_source id."""
    safe_filename = filename or "upload.bin"
    temp_name = f"__exec_upload_{eseguibile['id']}_{uuid.uuid4().hex[:8]}"
    owns_stream = isinstance(file_content, bytes)
    stream = io.BytesIO(file_content) if owns_stream else file_content
    try:
        file_source = file_source_service.create_from_upload(
            file_content=stream,
            filename=safe_filename,
            name=temp_name,
            description=f"Upload temporaneo per eseguibile {eseguibile['id']}",
            group_id=group_id,
        )
    finally:
        if owns_stream:
            stream.close()
    file_source_id = (
        file_source.id if hasattr(file_source, "id") else file_source["id"]
    )

    try:
        row = files_db.get_file_source(file_source_id, include_data=False)
        metadata = dict((row or {}).get("metadata") or {})
        metadata[UPLOAD_METADATA_KEY] = {
            "file_source_id": file_source_id,
            "filename": safe_filename,
            "eseguibile_id": eseguibile["id"],
            "user_id": user_id,
            "group_id": group_id,
            "input_key": input_key,
        }
        files_db.update_file_source_metadata(file_source_id, metadata)
    except Exception:
        file_source_service.delete_file_source(file_source_id)
        raise
    return file_source_id


def load_upload_metadata(file_source_id: str) -> Optional[Mapping[str, object]]:
    """Load durable upload metadata from the file_source DB row."""
    row = files_db.get_file_source(file_source_id, include_data=False)
    if not row:
        return None
    metadata = row.get("metadata") or {}
    upload = metadata.get(UPLOAD_METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(upload, dict):
        return None
    return {
        "file_source_id": file_source_id,
        "filename": upload.get("filename") or row.get("original_filename"),
        "eseguibile_id": upload.get("eseguibile_id"),
        "user_id": upload.get("user_id"),
        "group_id": upload.get("group_id") or row.get("group_id"),
        "input_key": upload.get("input_key"),
    }


def get_eseguibile_group_id(eseguibile_id: str) -> str:
    """Boundary: group_id dell'eseguibile per il tenant-binding a valle.

    Espone il dato cross-domain (lo usa il worker arricchisci) senza far
    bucare il leaf `eseguibili.db` ad altri domini.
    """
    row = eseguibili_db.get_eseguibile(eseguibile_id)
    return (row.get("group_id") or "") if row else ""


def get_eseguibile_for_access_check(
    eseguibile_id: str,
) -> Optional[eseguibili_db.EseguibileRow]:
    """Expose the executable ownership record through the domain service."""
    return eseguibili_db.get_eseguibile(eseguibile_id)


def complete_eseguibile_run(
    run_id: str,
    *,
    success: bool,
    output_metadata: JsonObject | None = None,
    error_message: str | None = None,
) -> None:
    """Close an executable run through the public domain boundary."""
    eseguibili_db.complete_eseguibile_run(
        run_id,
        success=success,
        output_metadata=output_metadata,
        error_message=error_message,
    )


def cancel_eseguibile_run(
    run_id: str,
    *,
    error_message: str = "Task annullato",
) -> None:
    """Cancel an executable run through the public domain boundary."""
    eseguibili_db.cancel_eseguibile_run(
        run_id,
        error_message=error_message,
    )


# EXECUTE — sostituisce sorgente nel canvas e lancia task

def execute(
    eseguibile: eseguibili_db.EseguibileRow,
    column_mapping: Sequence[Mapping[str, str]],
    file_source_id: Optional[str] = None,
    file_path: Optional[str] = None,
    upload_group_id: Optional[str] = None,
    output_format: Optional[str] = None,
    output_name: Optional[str] = None,
    task_id: Optional[str] = None,
    user_id: Optional[str] = None,
) -> EseguibileExecutionResult:
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
    from app.core.orchestrator.task_service import task_service

    if not file_source_id and not file_path:
        raise ValueError("File sorgente non specificato")

    # 1. Carica DataFrame originale.
    # - `file_source_id`: stream S3 -> spool bounded -> parser file-like.
    # - `file_path`: path-based legitimate (file gia' presente sul
    #   filesystem dal flow analyze/eseguibili).
    if file_source_id:
        df, _gdf = file_source_service.load_dataframe(
            file_source_id,
            expected_group_id=upload_group_id or eseguibile["group_id"],
        )
    else:
        df = dataframe_frame_io.load_dataframe_from_file(file_path)
    if df is None or df.empty:
        raise ValueError("File sorgente vuoto o illeggibile")

    # 2. Rinomina colonne secondo il mapping (solo quelle presenti
    # column_mapping è [{chain_column, file_column}]: inverto in {file_col: chain_col}
    rename_map: dict[str, str] = {}
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
    # Riusiamo il sistema file_source esistente — marchiamo con un nome speciale.
    #
    # Il nome diagnostico include il fingerprint del CSV reale. Non e' una
    # chiave d'idempotenza DB: la sicurezza dei retry resta nel task/run layer.
    # Serializziamo il DataFrame in memoria come CSV
    csv_buffer = io.BytesIO()
    df.to_csv(csv_buffer, index=False, encoding="utf-8")
    csv_payload = csv_buffer.getvalue()
    _df_signature = hashlib.sha256(
        str(eseguibile["id"]).encode("utf-8") + b"\0" + csv_payload
    ).hexdigest()[:12]
    temp_name = f"__exec_temp_{eseguibile['id']}_{_df_signature}"
    temp_filename = f"{temp_name}.csv"
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

    # Marca la riga come temporanea con TTL 24h. Il task beat
    # `cleanup_temp_files_task` cancella le righe is_temp=true scadute.
    # Fail closed: senza TTL un crash puo' lasciare il FileSource per sempre.
    try:
        eseguibili_db.mark_temp_file_source_ttl(temp_file_source_id)
    except Exception as ttl_err:
        file_source_service.delete_file_source(temp_file_source_id)
        raise RuntimeError(
            "Impossibile impostare la scadenza del file temporaneo"
        ) from ttl_err

    logger.info(
        f"FileSource temporaneo creato: {temp_file_source_id} "
        f"({len(df)} righe, {len(df.columns)} colonne, TTL 24h)",
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
    # Azzera campi delle altre tipologie (project_source, staticValues
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
    final_format = normalize_export_format(
        output_format or eseguibile.get("default_output_format") or "xlsx"
    )

    # Determina il nome file finale (priorità: utente > default eseguibile > nome eseguibile
    # Strippa eventuale estensione: il task la riaggiunge con `final_format`
    raw_name = (
        (output_name or "").strip()
        or (eseguibile.get("default_output_name") or "").strip()
        or eseguibile.get("name", "risultato")
    )
    # Sanitizza: rimuovi estensione, caratteri pericolosi per filesystem
    base_name = _sanitize_output_base_name(raw_name)

    # Applica al result node (sempre, non solo se l'utente ha specificato qualcosa
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

    try:
        execution_contract = PipelineExecutionContract(
            schema_version=2,
            project_id=str(eseguibile["id"]),
            project_type="arricchisci",
            group_id=str(eseguibile["group_id"]),
            canvas=canvas,
            artifact_refs={},
            execution_subject={
                "kind": "eseguibile",
                "id": str(eseguibile["id"]),
            },
            runtime_context={
                "cleanup_file_source_ids": [temp_file_source_id],
                "output_format": final_format,
                "output_name": base_name,
            },
        )
        task_info = task_service.create_enrichment_task(
            project_id=eseguibile["id"],
            canvas=canvas,
            output_format=final_format,
            force_recalculate=True,
            cleanup_file_source_ids=[temp_file_source_id],
            is_eseguibile=True,
            user_id=user_id,
            execution_contract=execution_contract.to_payload(),
            task_id=task_id,
        )
    except Exception:
        try:
            file_source_service.delete_file_source(temp_file_source_id)
        except Exception:
            logger.exception(
                "Cleanup FileSource temporaneo fallito dopo dispatch legacy"
            )
        raise

    logger.info(
        f"Task eseguibile avviato: task_id={task_info.task_id} eseguibile={eseguibile['id']}",
        op="eseguibili_execute"
    )

    return EseguibileExecutionResult(
        **{
            "async": True,
            "task_id": task_info.task_id,
            "project_id": eseguibile["id"],
            "temp_file_source_id": temp_file_source_id,
            "message": "Esecuzione avviata. Usa GET /api/tasks/{task_id} per il progresso.",
        }
    )


def _configure_runtime_output(
    canvas: dict,
    *,
    output_format: str,
    output_name: str,
) -> None:
    for node in canvas.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") not in {"result", "resultNode"}:
            continue
        data = node.setdefault("data", {})
        data["fileName"] = output_name
        data["format"] = output_format
        node["fileName"] = output_name
        node["format"] = output_format
        return


def resolve_version_manifest(
    eseguibile: eseguibili_db.EseguibileRow,
    version_id: Optional[str] = None,
) -> tuple[eseguibili_db.EseguibileVersionRow, ExecutableManifest]:
    """Versione (indicata o attiva) e manifest validato: lo stesso per la stima
    (eseguibili/preflight) e per l'avvio (execute_versioned)."""

    version = (
        eseguibili_db.get_eseguibile_version(
            version_id, eseguibile_id=eseguibile.id
        )
        if version_id
        else eseguibili_db.get_active_eseguibile_version(eseguibile.id)
    )
    if version is None:
        raise ValueError("Versione eseguibile non trovata")
    manifest = ExecutableManifest.model_validate(version.manifest)

    # Le versioni migrate dal contratto v1 non avevano il binding esplicito.
    # Lo ricostruiamo senza modificare lo snapshot persistito.
    if manifest.schema_version == 1 and not manifest.bindings and manifest.inputs:
        source = next(
            (
                node
                for node in version.canvas.get("nodes", [])
                if isinstance(node, dict)
                and node.get("type") in ("file_source", "project_source", "staticValues")
            ),
            None,
        )
        if source is None:
            raise ValueError("Canvas legacy senza sorgente sostituibile")
        manifest = manifest.model_copy(
            update={
                "bindings": [
                    ExecutableInputBinding(
                        input_key=manifest.inputs[0].key,
                        target_node_id=str(source.get("id")),
                        mode="data_source",
                    )
                ]
            }
        )
    return version, manifest


def discard_temp_file_sources(file_source_ids: Sequence[str]) -> None:
    """Elimina i file temporanei creati per gli input (best-effort)."""

    for file_source_id in file_source_ids:
        try:
            file_source_service.delete_file_source(file_source_id)
        except Exception:
            # Pulizia best-effort: un file temporaneo rimasto non deve
            # nascondere l'esito (avvio fallito o stima riuscita).
            pass


def execute_versioned(
    *,
    eseguibile: eseguibili_db.EseguibileRow,
    raw_inputs: Mapping[str, object],
    user_id: str,
    identity: RuntimeIdentity,
    version_id: Optional[str] = None,
    output_format: Optional[str] = None,
    output_name: Optional[str] = None,
    task_id: Optional[str] = None,
) -> ExecutableRunResult:
    """Valida una run v2 e la inoltra all'executor del tipo progetto.

    ``identity`` = l'utente autenticato che avvia (``RuntimeIdentity.
    authenticated(current_user)``): valorizza gli input «Utente che esegue».
    """

    from app.core.orchestrator.task_service import task_service

    version, manifest = resolve_version_manifest(eseguibile, version_id)

    canvas, cleanup_ids, input_summary = resolve_runtime_inputs(
        eseguibile_id=eseguibile.id,
        canvas=version.canvas,
        manifest=manifest,
        values=raw_inputs,
        group_id=eseguibile.group_id,
        identity=identity,
    )
    # Regola unica dei file temporanei (punto 5): da qui ogni uscita con
    # errore li elimina (except sotto); con successo li possiede il task.
    run_id: str | None = None
    try:
        runtime_connection_id = resolve_runtime_connection_id(
            manifest,
            raw_inputs=raw_inputs,
            group_id=eseguibile.group_id,
        )

        final_format = normalize_export_format(
            output_format or eseguibile.default_output_format or "xlsx"
        )
        raw_name = (
            (output_name or "").strip()
            or (eseguibile.default_output_name or "").strip()
            or eseguibile.name
        )
        base_name = _sanitize_output_base_name(raw_name)
        _configure_runtime_output(
            canvas,
            output_format=final_format,
            output_name=base_name,
        )

        reserved_task_id = task_id or task_service.reserve_task_id()
        run_id = eseguibili_db.create_eseguibile_run(
            eseguibile_id=eseguibile.id,
            version_id=version.id,
            group_id=eseguibile.group_id,
            user_id=user_id,
            input_summary=input_summary,
        )
        # The transactional outbox may publish or retry immediately after task
        # submission. Bind the stable id before handing control to it.
        eseguibili_db.bind_eseguibile_run_task(run_id, reserved_task_id)

        if manifest.project_type == "arricchisci":
            execution_contract = PipelineExecutionContract(
                schema_version=2,
                project_id=eseguibile.id,
                project_type=manifest.project_type,
                group_id=eseguibile.group_id,
                canvas=canvas,
                artifact_refs={},
                execution_subject={
                    "kind": "eseguibile",
                    "id": eseguibile.id,
                    "version_id": version.id,
                    "run_id": run_id,
                },
                runtime_context={
                    "default_connection_id": runtime_connection_id,
                    "cleanup_file_source_ids": cleanup_ids,
                    "output_format": final_format,
                    "output_name": base_name,
                    **runtime_bound_context(canvas),
                },
            )
            task_info = task_service.create_enrichment_task(
                project_id=eseguibile.id,
                canvas=canvas,
                output_format=final_format,
                force_recalculate=True,
                cleanup_file_source_ids=cleanup_ids,
                is_eseguibile=True,
                user_id=user_id,
                execution_contract=execution_contract.to_payload(),
                task_id=reserved_task_id,
            )
        else:
            execution_contract = PipelineExecutionContract(
                schema_version=2,
                project_id=eseguibile.id,
                project_type=manifest.project_type,
                group_id=eseguibile.group_id,
                canvas=canvas,
                artifact_refs={},
                execution_subject={
                    "kind": "eseguibile",
                    "id": eseguibile.id,
                    "version_id": version.id,
                    "run_id": run_id,
                },
                runtime_context={
                    "default_connection_id": runtime_connection_id,
                    "cleanup_file_source_ids": cleanup_ids,
                    "output_format": final_format,
                    "output_name": base_name,
                    **runtime_bound_context(canvas),
                },
            )
            task_info = task_service.create_pipeline_task(
                project_id=eseguibile.id,
                force_recalculate=True,
                triggered_by="eseguibile",
                user_id=user_id,
                execution_contract=execution_contract.to_payload(),
                task_id=reserved_task_id,
            )
        if task_info.task_id != reserved_task_id:
            raise RuntimeError(
                "Task service ha restituito un task_id diverso da quello riservato"
            )
    except Exception as exc:
        discard_temp_file_sources(cleanup_ids)
        if run_id is not None:
            eseguibili_db.complete_eseguibile_run(
                run_id,
                success=False,
                error_message=f"Avvio task fallito: {type(exc).__name__}",
            )
        raise
    return ExecutableRunResult(
        task_id=task_info.task_id,
        run_id=run_id,
        eseguibile_id=eseguibile.id,
        version_id=version.id,
    )
