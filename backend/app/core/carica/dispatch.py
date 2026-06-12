"""CARICA write dispatch — selezione sink + esecuzione, indipendente da Celery.

Estratto da ``workers/tasks/carica_task.py`` (#18/#20): la decisione "quale sink"
(db / arcgis / rest / datasource) e l'esecuzione della scrittura vivono qui, nel
modulo, non nel worker. Il worker resta un thin entry-point (run-logging,
target-lock, idempotency, stato Celery) e inietta un callback ``progress`` —
così ``carica/`` non dipende da Celery.

I cross-import (projects/auth/artifacts/datasources/connections) sono coerenti
col resto del modulo (vedi ``carica/executor.py``): infra condivisa, non
god-module.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath
from typing import Any, Callable, Mapping, Optional, cast

from typing_extensions import TypedDict

from app.shared.contracts.payloads import HeadersMap, JsonObject
from app.utils.logger import get_logger
from app.core.artifacts import (
    LatestProjectArtifactRef,
    SourceLoader,
    SourceLoadingError,
)
from app.core.auth.visibility import is_resource_visible
from app.core.carica import arcgis_write_boundary as arcgis_write_boundary_module
from app.core.carica.multilayer_artifact_registry import resolve_multilayer_artifact
from app.core.carica.executor import CaricaExecutor
from app.core.carica.rest_config_parser import parse_carica_rest_config
from app.core.carica.rest_write_boundary import execute_carica_rest_write
from app.core.connections.rest_sink import RestSinkHttpResponse
from app.core.connections.service import ConnectionService
from app.core.dataframes.export_writer import dataframe_to_export_chunks
from app.core.datasources.service import DataSourceService
from app.core.projects.runtime_context import get_project_runtime_context
from app.core.projects.service import ProjectService


logger = get_logger("app.core.carica.dispatch")

# Servizi iniettati dal composition root via CARICA_DISPATCH_BINDING
# (Epic D roadmap accoppiamento). Fallback per-chiamata per i contesti
# senza wiring (worker standalone, unit test su moduli parziali).
_project_service: Optional[ProjectService] = None
_connection_service: Optional[ConnectionService] = None
_datasource_service: Optional[DataSourceService] = None


def init_carica_dispatch(
    project_service: ProjectService,
    connection_service: ConnectionService,
    datasource_service: DataSourceService,
) -> None:
    """Inizializza il modulo. Chiamare da app_factory dopo setup."""
    global _project_service, _connection_service, _datasource_service
    _project_service = project_service
    _connection_service = connection_service
    _datasource_service = datasource_service


def _get_project_service() -> ProjectService:
    return _project_service if _project_service is not None else ProjectService()


def _get_connection_service() -> ConnectionService:
    return _connection_service if _connection_service is not None else ConnectionService()


def _get_datasource_service() -> DataSourceService:
    return _datasource_service if _datasource_service is not None else DataSourceService()


# Callback di progresso iniettato dal worker (astrae lo stato Celery): (percent, message).
# percent negativo = errore.
ProgressFn = Callable[[int, str], None]


class CaricaTaskResultPayload(TypedDict, total=False):
    status: str
    success: bool
    message: str
    error: str
    error_message: str
    project_id: str
    task_id: str
    run_id: str
    result_id: str
    output_file: str
    output_filename: str
    rows_written: int
    rows_count: int
    columns_count: int
    total_rows: int


def create_default_arcgis_feature_writer() -> Any:
    """Lazy wrapper kept patchable without importing ArcGIS services at registration."""
    return arcgis_write_boundary_module.create_default_arcgis_feature_writer()


def execute_carica_arcgis_write(**kwargs: Any) -> Any:
    """Lazy wrapper kept patchable without importing ArcGIS services at registration."""
    return arcgis_write_boundary_module.execute_carica_arcgis_write(**kwargs)


class _RequestsRestHttpClient:
    """Synchronous HTTP adapter for the REST write edge."""

    def request(
        self,
        *,
        method: str,
        url: str,
        headers: HeadersMap,
        json: Any,
        timeout: float,
    ) -> Any:
        import requests

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
        )
        response_body: Any
        try:
            response_body = response.json()
        except ValueError:
            response_body = None
        return RestSinkHttpResponse(
            status_code=response.status_code,
            body=response_body,
            text=response.text,
        )


def create_default_rest_http_client() -> _RequestsRestHttpClient:
    """Create the default HTTP client injected into the REST write boundary."""
    return _RequestsRestHttpClient()


def select_carica_write_branch(carica_node_data: JsonObject) -> str:
    """Return the write branch for a CARICA node without executing writes."""
    node_type = carica_node_data.get('type', '')
    dest_type = carica_node_data.get('dest_type', 'db')

    if node_type == 'datasource_node':
        return 'datasource'
    if dest_type == 'rest':
        return 'rest'
    if dest_type == 'arcgis' or node_type == 'carica_arcgis':
        return 'arcgis'
    return 'db'


def execute_datasource_write(
    df, carica_node_data, project_id, task_id, *, progress: ProgressFn,
):
    """Scrive il DataFrame su un DataSource (local/FTP/SFTP/SharePoint)."""
    datasource_id = carica_node_data.get('datasource_id', '')
    dest_folder = carica_node_data.get('dest_folder', '') or ''
    dest_filename = carica_node_data.get('dest_filename', '') or f'output_{project_id}'
    dest_format = carica_node_data.get('dest_format', 'csv') or 'csv'

    if not datasource_id:
        return {
            'status': 'connection_error',
            'success': False,
            'error_message': 'datasource_id mancante nel nodo',
            'rows_written': 0,
            'total_rows': len(df),
        }

    try:
        metadata = _get_project_service().get_project_metadata(project_id)
        group_id = getattr(metadata, "group_id", None) if metadata else None
        if not group_id or not is_resource_visible(
            "datasources",
            datasource_id,
            str(group_id),
            is_superadmin=False,
        ):
            return {
                'status': 'connection_error',
                'success': False,
                'error': 'datasource_not_visible',
                'message': 'DataSource non visibile al gruppo del progetto',
                'error_message': 'DataSource non visibile al gruppo del progetto',
                'rows_written': 0,
                'total_rows': len(df),
            }
    except Exception:
        logger.error('[SECURITY] Errore verifica visibilita DataSource', exc_info=True)
        return {
            'status': 'connection_error',
            'success': False,
            'error': 'datasource_visibility_check_failed',
            'message': 'Verifica DataSource fallita',
            'error_message': 'Verifica DataSource fallita',
            'rows_written': 0,
            'total_rows': len(df),
        }

    progress(20, f'Serializzazione DataFrame ({dest_format})...')
    progress(50, f'Scrittura su DataSource {datasource_id}...')

    try:
        ds_service = _get_datasource_service()

        # Componi path: dest_folder + dest_filename + .ext
        ext = f'.{dest_format}' if not dest_filename.endswith(f'.{dest_format}') else ''
        # Sanitizza per prevenire path traversal
        safe_folder = PurePosixPath(dest_folder).as_posix().replace('..', '').strip('/')
        safe_filename = PurePosixPath(dest_filename).name  # solo il nome, no directory
        path = f'{safe_folder}/{safe_filename}{ext}'.lstrip('/')

        ds_service.write_chunks(
            datasource_id, path, dataframe_to_export_chunks(df, dest_format),
        )

        progress(100, f'Scrittura completata: {path}')
        logger.info(f'Task CARICA DS {task_id} completato: {path}')

        return {
            'status': 'success',
            'success': True,
            'rows_written': len(df),
            'rows_failed': 0,
            'total_rows': len(df),
            'datasource_id': datasource_id,
            'path': path,
            'message': f'Scritti {len(df)} righe su {path}',
        }

    except Exception as e:
        logger.error(f'[CRASH] Errore scrittura DataSource: {e}', exc_info=True)
        # intent: boundary branch DataSource write / outcome: fail-closed sanitizzato
        return {
            'status': 'connection_error',
            'success': False,
            'error': 'carica_datasource_write_failed',
            'message': 'Errore scrittura DataSource (consulta log)',
            'error_message': 'Errore scrittura DataSource (consulta log)',
            'rows_written': 0,
            'total_rows': len(df),
        }


def execute_db_write(
    df, carica_node_data, project_id, task_id, triggered_by, run_id=None,
    *, progress: ProgressFn,
):
    """Esegue il caricamento su database (percorso originale).

    ``run_id`` propagato all'executor come self-defense layer (N15).
    """
    def progress_callback(batch_progress):
        overall_percent = 10 + int(batch_progress.percent * 0.85)
        progress(overall_percent, batch_progress.message)

    # Passa connection_service per decriptare correttamente le password
    executor = CaricaExecutor(connection_service=_get_connection_service())

    result = executor.execute(
        df=df,
        node_data=carica_node_data,
        project_id=project_id,
        progress_callback=progress_callback,
        triggered_by=triggered_by,
        run_id=run_id,
    )

    result_dict = result.to_dict()
    is_success = result.status.value in ('success', 'completed', 'partial_success')
    result_dict["success"] = is_success
    result_dict["rows_count"] = result.rows_written
    result_dict["columns_count"] = result_dict.get("columns_count") or len(df.columns)
    if not is_success and not result_dict.get("message"):
        result_dict["message"] = result_dict.get("error_message", f"Errore: {result.status.value}")

    progress(
        100 if is_success else -1,
        f"Completato: {result.rows_written} righe scritte" if is_success
        else result_dict.get("error_message", "Errore"),
    )

    logger.info(f"Task CARICA DB {task_id} completato: {result.status.value}")
    return result_dict


def execute_rest_write(
    df: Any,
    carica_node_data: JsonObject,
    project_id: str,
    task_id: str,
    *,
    progress: ProgressFn,
) -> CaricaTaskResultPayload:
    """Coordinate REST write progress while the domain owns REST payload writes."""
    progress(15, "Preparazione scrittura REST...")

    try:
        parsed_config = parse_carica_rest_config(carica_node_data)
        progress(25, "Scrittura REST...")
        outcome = execute_carica_rest_write(
            df=df,
            config=parsed_config.sink_config,
            http_client=create_default_rest_http_client(),
        )
        result = cast(CaricaTaskResultPayload, outcome.to_task_result())

        is_success = result.get("success", False)
        rows = result.get("rows_written", 0) or 0
        if not is_success:
            progress(
                -1,
                result.get("error_message") or result.get("message") or "Errore scrittura REST",
            )
            return result

        progress(100, f"REST completato: {rows} righe scritte")
        logger.info(f"Task CARICA REST {task_id} completato: {rows} righe")
        return result

    except Exception as e:
        logger.error(f"[GESTITO] Errore scrittura REST: {e}", exc_info=True)
        return {
            "status": "write_error",
            "success": False,
            "error": "carica_rest_write_failed",
            "message": "Errore scrittura REST (consulta log)",
            "error_message": "Errore scrittura REST (consulta log)",
            "rows_written": 0,
            "total_rows": len(df),
        }


def maybe_multilayer_arcgis_write(
    project_id: str,
    canvas: JsonObject,
    carica_node_data: JsonObject,
    task_id: str,
    *,
    progress: ProgressFn,
) -> Optional[JsonObject]:
    """Pubblica N layer su ArcGIS se l'artefatto upstream e' un bundle multi-layer.

    Recupera i BYTES grezzi dell'ultimo artefatto del progetto (non il DataFrame
    coerced) e li interroga via il registry multi-layer: CARICA resta generico,
    gli adapter verticali (es. IMDF ``plenora.indoor.bundle``) sono registrati al
    composition root. Se nessun adapter riconosce il payload -> ``None`` e il
    chiamante ricade sul path mono-layer (DataFrame).
    """
    try:
        _ctx = get_project_runtime_context(project_id)
        group_id = _ctx.group_id if _ctx else None
    except Exception as exc:
        logger.warning(f"[GESTITO] multilayer: lookup group_id fallito: {exc}")
        return None
    if not group_id:
        logger.error(
            f"[SECURITY] multilayer: group_id non determinabile per {project_id}, "
            f"skip detection bundle (fallback mono-layer)"
        )
        return None

    try:
        fetched = SourceLoader().resolve_project_file_bytes(
            LatestProjectArtifactRef(project_id=project_id),
            expected_group_id=group_id,
        )
    except SourceLoadingError as exc:
        logger.error(f"[SECURITY] multilayer: fetch artefatto fallito: {exc}")
        return None
    except Exception as exc:
        logger.warning(f"[GESTITO] multilayer: fetch artefatto fallito: {exc}")
        return None
    if not fetched:
        return None

    raw_bytes, _filename = fetched
    try:
        payload = json.loads(raw_bytes)
    except (ValueError, UnicodeDecodeError):
        return None  # non-JSON -> non e' un bundle multi-layer
    if not isinstance(payload, dict):
        return None

    artifact = resolve_multilayer_artifact(payload)
    if artifact is None:
        return None  # nessun adapter registrato riconosce il payload

    progress(
        10,
        f"Bundle multi-layer: {len(artifact.layers)} layer. Avvio publish ArcGIS...",
    )

    raw_op = carica_node_data.get("arcgis_operation") or "create_service"
    operation = "create_service" if raw_op == "create_service" else (
        "append" if raw_op in ("add", "append") else raw_op
    )
    request = arcgis_write_boundary_module.CaricaArcGISMultiLayerRequest(
        project_id=project_id,
        connection_id=carica_node_data.get("arcgis_connection_id", ""),
        operation=operation,
        source_srid=carica_node_data.get("source_srid"),
        service_name=carica_node_data.get("arcgis_new_service_name", ""),
        service_url=carica_node_data.get("arcgis_service_url", ""),
        folder_id=carica_node_data.get("arcgis_folder_id", ""),
        renderers=artifact.renderers_by_layer_name or None,
    )

    outcome = arcgis_write_boundary_module.execute_carica_arcgis_multilayer_write(
        layers=list(artifact.layers),
        request=request,
        feature_writer=create_default_arcgis_feature_writer(),
    )
    result = outcome.to_task_result()
    if outcome.success:
        progress(
            100,
            f"ArcGIS multi-layer completato: {outcome.rows_written} features",
        )
        logger.info(
            f"Task CARICA ArcGIS multi-layer {task_id} completato: "
            f"{outcome.rows_written} features"
        )
    else:
        progress(-1, outcome.error_message or outcome.message)
    return result


def execute_arcgis_write(
    df: Any,
    carica_node_data: JsonObject,
    project_id: str,
    task_id: str,
    *,
    progress: ProgressFn,
) -> Mapping[str, object]:
    """Coordinate ArcGIS write progress while the domain owns provider writes."""
    progress(15, "Preparazione scrittura ArcGIS...")

    operation = carica_node_data.get("arcgis_operation", "add")
    request = arcgis_write_boundary_module.CaricaArcGISWriteRequest(
        project_id=project_id,
        connection_id=carica_node_data.get("arcgis_connection_id", ""),
        operation=operation,
        geometry_column=carica_node_data.get("geometry_column", "geometry"),
        source_srid=carica_node_data.get("source_srid"),
        batch_size=carica_node_data.get("batch_size"),
        column_mappings=carica_node_data.get("column_mappings", []),
        layer_url=carica_node_data.get("arcgis_layer_url", ""),
        match_column=carica_node_data.get("arcgis_match_column", ""),
        service_name=carica_node_data.get("arcgis_new_service_name", ""),
        layer_name=carica_node_data.get("arcgis_new_layer_name", "Layer 0"),
        folder_id=carica_node_data.get("arcgis_folder_id", ""),
    )

    progress(25, f"Scrittura ArcGIS ({operation})...")

    try:
        if operation == "create_service":
            progress(30, f"Creazione servizio '{request.service_name}'...")
        else:
            progress(30, f"Scrittura features ({operation})...")

        outcome = execute_carica_arcgis_write(
            df=df,
            request=request,
            feature_writer=create_default_arcgis_feature_writer(),
        )
        result = outcome.to_task_result()
        rows = outcome.rows_written

        if not outcome.success:
            progress(-1, outcome.error_message or outcome.message)
            return result

        progress(100, f"ArcGIS completato: {rows} features")
        logger.info(f"Task CARICA ArcGIS {task_id} completato: {rows} features")
        return result

    except Exception as e:
        # Canale admin log: preserva str(e) per postmortem
        logger.error(f"[GESTITO] Errore scrittura ArcGIS: {e}", exc_info=True)
        # intent: boundary branch ArcGIS write / outcome: fail-closed sanitizzato
        return {
            "status": "write_error",
            "success": False,
            "error": "carica_arcgis_write_failed",
            "message": "Errore scrittura ArcGIS (consulta log)",
            "error_message": "Errore scrittura ArcGIS (consulta log)",
            "rows_written": 0,
            "total_rows": len(df),
        }
