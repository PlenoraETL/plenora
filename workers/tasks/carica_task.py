"""
Task Celery per il modulo CARICA — esecuzione asincrona con progresso Redis.

Architettura
============
Questo modulo definisce il task Celery che esegue il caricamento su database
in background. Viene invocato dal router /api/tasks quando l'utente avvia
il caricamento dalla UI o dal CRON scheduler.

Flusso di esecuzione:
1. Il router crea il task Celery con i parametri (project_id, canvas, config)
2. Il worker Celery riceve il task e lo esegue in un processo separato
3. Durante l'esecuzione, il task aggiorna il progresso in Redis tramite
   task.update_state(state='PROGRESS', meta={'percent': N, 'message': '...'})
4. Il frontend fa polling su GET /api/tasks/{task_id} per leggere il progresso
5. Al completamento, il risultato viene salvato in Redis

Il pattern è identico a enrichment_task.py di ARRICCHISCI per garantire
consistenza nell'interfaccia di monitoraggio del frontend.

Uso:
    task = execute_carica_task.delay(project_id, canvas_dict, carica_config)
    # Frontend: GET /api/tasks/{task.id} per polling
"""

from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.config import settings
import os
import sys
import time as _time
import traceback
import uuid as _uuid
from typing import Any, Dict, Optional

from workers.celery_app import celery_app, BaseTaskWithRetry


logger = get_logger("workers.carica_task")


@celery_app.task(
    bind=True,
    base=BaseTaskWithRetry,
    name="carica.execute",
    max_retries=3,
    default_retry_delay=30,
    soft_time_limit=settings.CELERY_TASK_TIMEOUT,
    time_limit=settings.CELERY_TASK_HARD_LIMIT,
)
def execute_carica_task(
    self,
    project_id: str,
    canvas: Dict[str, Any],
    carica_node_data: Dict[str, Any],
    output_format: str = "geojson",
    force_recalculate: bool = False,
    triggered_by: str = "manual",
) -> Dict[str, Any]:
    """Task asincrono per eseguire il caricamento su database.

    Args:
        project_id: ID del progetto
        canvas: Canvas state completo con nodi e edges
        carica_node_data: Dati del nodo CARICA (configurazione)
        output_format: Formato output del nodo Result upstream
        force_recalculate: Se True, riesegue la pipeline upstream
        triggered_by: "manual" o "cron"

    Returns:
        Dict con risultato dell'esecuzione (WriteResult.to_dict())
    """
    task_id = self.request.id
    run_id = str(_uuid.uuid4())
    _run_start = _time.time()

    logger.info(f"Task CARICA {task_id} avviato per progetto {project_id}")

    # --- Leggi metadata progetto ---
    _group_id = ""
    try:
        from app.services.project_service import ProjectService as _PS
        _meta = _PS().get_project_metadata(project_id)
        _group_id = getattr(_meta, "group_id", "") if _meta else ""
    except Exception:
        pass

    # --- Apri run su log.db ---
    open_run(
        run_id=run_id,
        project_id=project_id,
        group_id=_group_id,
        module="carica",
        triggered_by=triggered_by,
    )
    set_run_context(run_id=run_id, project_id=project_id)

    # === 1. Aggiorna stato: PARTENZA ===
    _update_progress(self, 0, "Inizializzazione CARICA...")

    try:
        # === 2. Carica il DataFrame upstream ===
        _update_progress(self, 5, "Caricamento dati dal nodo Output...")
        df = _load_upstream_data(project_id, canvas, force_recalculate, output_format)

        if df is None:
            logger.warning("Nessun dato dal nodo upstream — DataFrame None")
            close_run(
                run_id=run_id, status="failure",
                duration_ms=int((_time.time() - _run_start) * 1000),
                error_module="carica",
                error_message="Nessun dato disponibile dal nodo Output upstream.",
            )
            return {
                "status": "validation_error",
                "success": False,
                "error_message": "Nessun dato disponibile dal nodo Output upstream.",
                "rows_written": 0,
                "total_rows": 0,
            }

        if len(df) == 0:
            # 0 righe è un caso legittimo — completato con successo senza scrivere nulla
            logger.info("Dataset vuoto (0 righe) — CARICA completato senza scritture")
            close_run(
                run_id=run_id, status="success",
                rows_out=0,
                duration_ms=int((_time.time() - _run_start) * 1000),
            )
            return {
                "status": "completed",
                "success": True,
                "message": "Dataset vuoto: nessuna riga da scrivere.",
                "rows_written": 0,
                "total_rows": 0,
            }

        logger.info(f"DataFrame caricato: {len(df)} righe, {len(df.columns)} colonne")
        _update_progress(
            self, 10,
            f"Dati caricati: {len(df)} righe. Avvio scrittura..."
        )

        node_type = carica_node_data.get('type', '')
        dest_type = carica_node_data.get('dest_type', 'db')

        if node_type == 'datasource_node':
            result = _execute_datasource_write(self, df, carica_node_data, project_id, task_id)
        elif dest_type == 'arcgis' or node_type == 'carica_arcgis':
            result = _execute_arcgis_write(self, df, carica_node_data, project_id, task_id)
        else:
            result = _execute_db_write(self, df, carica_node_data, project_id, task_id, triggered_by)

        # --- Step tracking + close run ---
        is_success = result.get("success", False)
        rows_written = result.get("rows_written", 0) or 0
        elapsed_ms = int((_time.time() - _run_start) * 1000)

        append_step({
            "module":      "carica",
            "status":      "success" if is_success else "failure",
            "rows_out":    rows_written,
            "duration_ms": elapsed_ms,
            "error":       result.get("error_message") if not is_success else None,
        })
        close_run(
            run_id=run_id,
            status="success" if is_success else "failure",
            rows_out=rows_written,
            duration_ms=elapsed_ms,
            steps=[],   # steps già nel context thread-local non accessibili qui — opzionale
            error_module="carica" if not is_success else None,
            error_message=result.get("error_message") if not is_success else None,
        )
        return result

    except Exception as e:
        error_msg = f"Errore task CARICA: {str(e)}"
        error_trace = traceback.format_exc()
        elapsed_ms = int((_time.time() - _run_start) * 1000)
        logger.error(f"[CRASH] {error_msg}\n{error_trace}")

        # --- debug_payload ---
        try:
            from app.utils.debug_payload_builder import DebugPayloadBuilder
            debug_payload = DebugPayloadBuilder().build(
                run_id=run_id,
                project_id=project_id,
                steps=[],
                failure={
                    "node_id":        None,
                    "node_type":      "carica",
                    "node_label":     "CARICA",
                    "module":         "carica",
                    "error_type":     type(e).__name__,
                    "error_message":  str(e),
                    "traceback":      error_trace,
                    "occurred_at_ms": elapsed_ms,
                },
            )
        except Exception:
            debug_payload = None

        close_run(
            run_id=run_id,
            status="failure",
            duration_ms=elapsed_ms,
            error_module="carica",
            error_message=error_msg,
            debug_payload=debug_payload,
        )
        _update_progress(self, -1, error_msg)
        return {
            "status": "write_error",
            "success": False,
            "message": error_msg,
            "error_message": error_msg,
            "error_details": error_trace,
            "rows_written": 0,
            "total_rows": 0,
        }
    finally:
        # FIX: chiudi le connessioni SQLite thread-local del worker Celery.
        # Senza questa pulizia, ogni esecuzione lascia 2 connessioni aperte
        # (config.db + intermediate.db) nel thread.local, che vengono riusate
        # dal worker pool finché il file handle si esaurisce o il DB resta
        # locked. Si manifesta dopo ~50 esecuzioni come SQLITE_BUSY o crash
        # del worker.
        try:
            from app.core.intermediate.db import close_connection as _close_idb
            _close_idb()
        except Exception:
            pass
        try:
            from app.db.config_db_service import close_connection as _close_cfg
            _close_cfg()
        except Exception:
            pass


def _execute_datasource_write(task, df, carica_node_data, project_id, task_id):
    """Scrive il DataFrame su un DataSource (local/FTP/SFTP/SharePoint)."""
    import io

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

    _update_progress(task, 20, f'Serializzazione DataFrame ({dest_format})...')

    # Serializza DataFrame in bytes
    buf = io.BytesIO()
    if dest_format == 'csv':
        buf.write(df.to_csv(index=False).encode('utf-8'))
    elif dest_format in ('xlsx', 'xls'):
        df.to_excel(buf, index=False)
    elif dest_format == 'json':
        buf.write(df.to_json(orient='records', force_ascii=False).encode('utf-8'))
    elif dest_format == 'geojson':
        buf.write(df.to_json(orient='records', force_ascii=False).encode('utf-8'))
    else:
        buf.write(df.to_csv(index=False).encode('utf-8'))
    content = buf.getvalue()

    _update_progress(task, 50, f'Scrittura su DataSource {datasource_id}...')

    try:
        from app.core.datasources.service import DataSourceService
        ds_service = DataSourceService()

        # Componi path: dest_folder + dest_filename + .ext
        ext = f'.{dest_format}' if not dest_filename.endswith(f'.{dest_format}') else ''
        path = f'{dest_folder}/{dest_filename}{ext}'.lstrip('/')

        ds_service.write_file(datasource_id, path, content)

        _update_progress(task, 100, f'Scrittura completata: {path}')
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
        return {
            'status': 'connection_error',
            'success': False,
            'error_message': str(e),
            'rows_written': 0,
            'total_rows': len(df),
        }


def _execute_db_write(task, df, carica_node_data, project_id, task_id, triggered_by):
    """Esegue il caricamento su database (percorso originale)."""
    from app.core.carica.executor import CaricaExecutor
    from app.services.connection_service import ConnectionService

    def progress_callback(batch_progress):
        overall_percent = 10 + int(batch_progress.percent * 0.85)
        _update_progress(task, overall_percent, batch_progress.message)

    # Passa connection_service per decriptare correttamente le password
    executor = CaricaExecutor(connection_service=ConnectionService())

    result = executor.execute(
        df=df,
        node_data=carica_node_data,
        project_id=project_id,
        progress_callback=progress_callback,
        triggered_by=triggered_by,
    )

    result_dict = result.to_dict()
    is_success = result.status.value in ('success', 'completed', 'partial_success')
    result_dict["success"] = is_success
    result_dict["rows_count"] = result.rows_written
    result_dict["columns_count"] = result_dict.get("columns_count") or len(df.columns)
    if not is_success and not result_dict.get("message"):
        result_dict["message"] = result_dict.get("error_message", f"Errore: {result.status.value}")
    
    _update_progress(task, 100 if is_success else -1, 
                     f"Completato: {result.rows_written} righe scritte" if is_success 
                     else result_dict.get("error_message", "Errore"))
    
    logger.info(f"Task CARICA DB {task_id} completato: {result.status.value}")
    return result_dict


def _execute_arcgis_write(task, df, carica_node_data, project_id, task_id):
    """Esegue il caricamento su ArcGIS Feature Service."""
    from app.core.features.features_service import FeaturesService
    from app.utils.dataframe import save_dataframe
    from app.config import settings

    _update_progress(task, 15, "Preparazione scrittura ArcGIS...")

    connection_id = carica_node_data.get('arcgis_connection_id', '')
    operation = carica_node_data.get('arcgis_operation', 'add')
    geometry_column = carica_node_data.get('geometry_column', 'geometry')
    source_srid = carica_node_data.get('source_srid')
    batch_size = carica_node_data.get('batch_size')  # None = usa default WRITE_BATCH_SIZE=1000

    if not connection_id:
        return {
            "status": "validation_error",
            "success": False,
            "error_message": "Nessuna connessione ArcGIS configurata",
            "rows_written": 0,
            "total_rows": len(df),
        }

    # Usa write_features_from_df che accetta DataFrame direttamente
    # evitando il round-trip su file (il blob è già stato consumato da _load_upstream_data)

    features_service = FeaturesService(save_function=save_dataframe)

    _update_progress(task, 25, f"Scrittura ArcGIS ({operation})...")

    try:
        if operation == 'create_service':
            service_name = carica_node_data.get('arcgis_new_service_name', '')
            layer_name = carica_node_data.get('arcgis_new_layer_name', 'Layer 0')
            folder_id = carica_node_data.get('arcgis_folder_id', '')

            if not service_name:
                return {
                    "status": "validation_error",
                    "success": False,
                    "error_message": "Nome servizio ArcGIS non specificato",
                    "rows_written": 0,
                    "total_rows": len(df),
                }

            _update_progress(task, 30, f"Creazione servizio '{service_name}'...")

            result = features_service.create_service_from_df(
                connection_id=connection_id,
                service_name=service_name,
                layer_name=layer_name,
                df=df,
                geometry_column=geometry_column,
                source_srid=source_srid,
                folder_id=folder_id,
            )

        else:
            # add, update, delete
            layer_url = carica_node_data.get('arcgis_layer_url', '')
            if not layer_url:
                return {
                    "status": "validation_error",
                    "success": False,
                    "error_message": "URL layer ArcGIS non specificato",
                    "rows_written": 0,
                    "total_rows": len(df),
                }

            match_column = carica_node_data.get('arcgis_match_column', '')
            _update_progress(task, 30, f"Scrittura features ({operation})...")

            result = features_service.write_features_from_df(
                connection_id=connection_id,
                layer_url=layer_url,
                operation=operation,
                df=df,
                geometry_column=geometry_column,
                source_srid=source_srid,
                batch_size=batch_size,
                match_column=match_column,
            )

        rows = result.get("feature_count", result.get("count", 0))
        _update_progress(task, 100, f"ArcGIS completato: {rows} features")
        logger.info(f"Task CARICA ArcGIS {task_id} completato: {rows} features")
        return {
            "status": "completed",
            "success": True,
            "project_id": project_id,
            "rows_written": rows,
            "rows_count": rows,
            "total_rows": len(df),
            "columns_count": len(df.columns),
            "message": result.get("message", f"Scrittura ArcGIS completata: {rows} features"),
            "service_url": result.get("service_url", ""),
        }

    except Exception as e:
        error_msg = f"Errore scrittura ArcGIS: {str(e)}"
        logger.error(f"[GESTITO] {error_msg}", exc_info=True)
        return {
            "status": "write_error",
            "success": False,
            "message": error_msg,
            "error_message": error_msg,
            "rows_written": 0,
            "total_rows": len(df),
        }


def _update_progress(task, percent: int, message: str):
    """Aggiorna il progresso del task in Redis.

    Usa sempre lo stato 'PROGRESS' (mai 'FAILURE') per evitare che Celery
    interpreti il task come terminato. Il frontend determina lo stato
    reale dal contenuto del messaggio e dalla percentuale.

    Un percent negativo indica errore.
    """
    try:
        task.update_state(
            state='PROGRESS',
            meta={
                'progress': max(0, percent),
                'message': message,
                'is_error': percent < 0,
            }
        )
    except Exception as e:
        logger.warning(f"Errore aggiornamento progresso: {e}")


def _load_upstream_data(
    project_id: str,
    canvas: Dict[str, Any],
    force_recalculate: bool,
    output_format: str,
) -> Optional["pd.DataFrame"]:
    """Carica il DataFrame dal nodo Result upstream.

    Strategia:
    1. Cerca nel DB SQLite (get_result_as_tempfile)
    2. Se force_recalculate o non trova → riesegui la pipeline
    3. Dopo ricalcolo rilegge dal DB
    """
    import pandas as pd

    try:
        from app.utils.result_store import get_result_as_tempfile
        from app.utils.dataframe import load_dataframe_from_file

        # Cerca risultato nel DB
        if not force_recalculate:
            tmp = get_result_as_tempfile(project_id)
            if tmp:
                logger.info(f"Caricamento output dal DB per {project_id}")
                df = load_dataframe_from_file(tmp)
                try:
                    import os as _os
                    _os.unlink(str(tmp))
                except Exception:
                    pass
                return df

        # Nessun risultato in cache o force_recalculate → riesegui pipeline

        from app.core.orchestrator.service import OrchestratorService
        from app.services.project_service import ProjectService
        from app.services.connection_service import ConnectionService
        from app.core.estrai.query_generator import QueryGenerator
        from app.db.query_executor import create_executor
        from app.core.features.features_service import FeaturesService
        from app.utils.dataframe import save_dataframe

        project_service = ProjectService()
        connection_service = ConnectionService()
        # FIX: passa save_function=save_dataframe altrimenti il blob non viene salvato
        # e get_result_as_tempfile() restituisce None dopo il ricalcolo
        executor = create_executor(save_function=save_dataframe)
        generator = QueryGenerator()
        features_service = FeaturesService(save_function=save_dataframe)

        orchestrator = OrchestratorService(
            project_service=project_service,
            executor=executor,
            generator=generator,
            connection_service=connection_service,
            features_service=features_service,
        )

        result = orchestrator.execute_pipeline(
            project_id=project_id,
            force_recalculate=force_recalculate,
            skip_carica=True,
        )

        # Controlla se la pipeline ha fallito — errore gestito, non crash
        if not result or not result.get("success"):
            pipeline_error = (result or {}).get("message", "Pipeline fallita senza messaggio")
            logger.error(
                f"[GESTITO] Pipeline upstream fallita per {project_id}: {pipeline_error}. "
                f"Impossibile procedere con CARICA."
            )
            return None

        # Se la pipeline ha avuto successo, leggi il blob dal DB
        tmp = get_result_as_tempfile(project_id)
        if tmp:
            df = load_dataframe_from_file(tmp)
            try:
                import os as _os
                _os.unlink(str(tmp))
            except Exception:
                pass
            return df

        # Fallback: DataFrame inline dal risultato (MANIPOLA/ARRICCHISCI)
        if "dataframe" in result:
            return result["dataframe"]

        # Rileggi dal DB una seconda volta (race condition)
        tmp = get_result_as_tempfile(project_id)
        if tmp:
            df = load_dataframe_from_file(tmp)
            try:
                import os as _os
                _os.unlink(str(tmp))
            except Exception:
                pass
            return df

        # A questo punto la pipeline ha avuto successo ma non ha prodotto output
        logger.error(
            f"[GESTITO] Pipeline upstream completata ma nessun output trovato per {project_id}. "
            f"Verificare che il nodo Result salvi correttamente il file."
        )
        return None

    except ImportError as e:
        logger.error(f"[CRASH] Import error: {e}", exc_info=True)
        # Fallback: prova a caricare direttamente
        return _load_from_project_dir(project_id)

    except Exception as e:
        logger.error(f"[CRASH] Errore caricamento dati upstream: {e}", exc_info=True)
        return _load_from_project_dir(project_id)


def _load_from_project_dir(project_id: str) -> Optional["pd.DataFrame"]:
    """Fallback: carica dal DB SQLite."""
    try:
        from app.utils.result_store import get_result_as_tempfile
        from app.utils.dataframe import load_dataframe_from_file
        tmp = get_result_as_tempfile(project_id)
        if tmp:
            df = load_dataframe_from_file(tmp)
            try:
                import os as _os
                _os.unlink(str(tmp))
            except Exception:
                pass
            return df
    except Exception as e:
        logger.error(f"[CRASH] Errore fallback caricamento dal DB: {e}", exc_info=True)
    return None


def _find_latest_output(directory: str) -> Optional[str]:
    """Trova il file di output più recente nella directory.

    Supporta: CSV, JSON, GeoJSON, GeoPackage, Shapefile, Excel.
    """
    SUPPORTED_EXTENSIONS = {
        '.csv', '.json', '.geojson', '.gpkg', '.shp',
        '.xlsx', '.xls', '.parquet',
    }

    latest_file = None
    latest_time = 0

    try:
        for fname in os.listdir(directory):
            fpath = os.path.join(directory, fname)
            _, ext = os.path.splitext(fname.lower())
            if ext in SUPPORTED_EXTENSIONS and os.path.isfile(fpath):
                mtime = os.path.getmtime(fpath)
                if mtime > latest_time:
                    latest_time = mtime
                    latest_file = fpath
    except Exception as e:
        logger.error(f"[CRASH] Errore scansione directory output: {e}", exc_info=True)

    return latest_file