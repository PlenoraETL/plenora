"""
Task Celery per arricchimento dati asincrono.

Architettura
============
Questo modulo definisce il task Celery che esegue l'arricchimento batch
in background. Viene invocato dal router /api/tasks quando l'utente
avvia un arricchimento completo.

Flusso di esecuzione:
1. Il router crea il task Celery con i parametri (api_id, project_id, options)
2. Il worker Celery riceve il task e lo esegue in un processo separato
3. Durante l'esecuzione, il task aggiorna il progresso in Redis tramite
   task.update_state(state='PROGRESS', meta={'percent': N, 'message': '...'})
4. Il frontend fa polling su GET /api/tasks/{task_id} per leggere il progresso
5. Al completamento, il risultato (path file output) viene salvato in Redis

Componenti:
- run_enrichment_task(): task Celery principale
- _progress_callback(): callback passato a EnrichmentExecutor per aggiornamenti
- _save_result(): salvataggio DataFrame arricchito nel formato richiesto

Gestione errori:
- Timeout configurabile per task lungo
- Retry automatico su errori transitori (rete, rate limiting)
- Stato FAILURE con dettagli errore salvati in Redis
"""

import os
import time
import json
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime
from celery import current_task
from workers.celery_app import celery_app
from app.config import settings
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.config import settings
from app.core.arricchisci.enrichment_service import EnrichmentExecutor, generate_error_report
from app.core.arricchisci.models import EnrichmentOptions, EnrichmentResult

logger = get_logger(__name__)


@celery_app.task(bind=True, name="tasks.execute_enrichment")
def execute_enrichment_task(
    self,
    project_id: str,
    canvas: Dict,
    output_format: str = "csv",
    force_recalculate: bool = False,
    cleanup_file_source_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Task asincrono per eseguire l'arricchimento dati.

    Args:
        project_id: ID del progetto
        canvas: Canvas con nodi e edges
        output_format: Formato output (csv, xlsx, geojson, etc.)
        force_recalculate: Se True, riesegue anche se esiste cache
        cleanup_file_source_ids: Lista di file_source_id da eliminare al termine
            del task (sia in caso di successo che di errore). Usato dagli eseguibili
            per rimuovere i file temporanei creati durante l'esecuzione on-demand.

    Returns:
        Dict con risultato dell'esecuzione
    """
    task_id = self.request.id
    start_time = time.time()
    
    logger.info(f"[Task {task_id}] Avvio enrichment per progetto {project_id}", project_id=project_id)
    
    # Aggiorna stato: STARTED
    update_task_progress(self, 0, "Inizializzazione...")
    
    # Variabili per statistiche enrichment
    enrichment_stats = None
    error_report_file = None
    
    # Apre run in intermediate.db (non bloccante)
    import uuid as _uuid
    _run_id = str(_uuid.uuid4())
    _run_start = time.time()
    _group_id = ""
    try:
        from app.core.intermediate import db as _idb
        from app.services.project_service import ProjectService as _PS
        _meta = _PS().get_project_metadata(project_id)
        _group_id = getattr(_meta, 'group_id', 'unknown') if _meta else 'unknown'
        _idb.create_run(run_id=_run_id, canvas_id=project_id, group_id=_group_id)
    except Exception as _rie:
        logger.warning(f"[Task {task_id}] Intermediate: apertura run fallita (non bloccante): {_rie}")
        _run_id = None

    # --- Apri run su log.db ---
    open_run(
        run_id=_run_id or task_id,
        project_id=project_id,
        group_id=_group_id,
        module="arricchisci",
        triggered_by="manual",
    )
    set_run_context(run_id=_run_id or task_id, project_id=project_id)

    try:
        # Import qui per evitare circular imports
        from app.core.arricchisci.api_connector import api_connection_service
        
        nodes = canvas.get("nodes", [])
        edges = canvas.get("edges", [])
        
        if not nodes:
            raise ValueError("Nessun nodo nel canvas")

        update_task_progress(self, 5, "Caricamento dati...")

        # Carica API connections
        api_connections = {}
        for api in api_connection_service.list_api_connections():
            full_api = api_connection_service.get_api_connection(api["id"], include_token=True)
            if full_api:
                api_connections[api["id"]] = full_api
        
        update_task_progress(self, 10, "API caricate, inizio elaborazione...")
        
        # Ordine esecuzione topologico
        execution_order = get_execution_order(nodes, edges)
        node_results = {}
        
        # Esegui nodi in ordine
        for idx, node_id in enumerate(execution_order):
            node = next((n for n in nodes if n["id"] == node_id), None)
            if not node:
                continue
            
            node_type = node.get("type")
            # Merge: campi flat del nodo (da Pydantic) + node['data'] (da frontend)
            # I campi in node['data'] hanno precedenza perché sono quelli salvati dal frontend
            node_flat = {k: v for k, v in node.items() if k not in ("id", "type", "position", "data", "config")}
            node_data = {**node_flat, **(node.get("data") or {})}
            
            # Calcola progress base per questo nodo
            node_progress = 10 + int((idx / len(execution_order)) * 80)
            
            if node_type == "project_source":
                update_task_progress(self, node_progress, f"Caricamento sorgente dati...")
                result = load_source_project(node_data, force_recalculate)
                node_results[node_id] = result
                
            elif node_type == "file_source":
                # Controlla se downstream è executable → skip DataFrame parsing
                downstream_edges = [e for e in edges if e["source"] == node_id]
                is_for_executable = False
                for de in downstream_edges:
                    target_node = next((n for n in nodes if n["id"] == de["target"]), None)
                    if target_node and target_node.get("type") == "api_enrich":
                        target_api_id = target_node.get("data", {}).get("api_id")
                        target_api_config = api_connections.get(target_api_id, {})
                        if target_api_config.get("connection_type") == "executable":
                            is_for_executable = True
                            break
                
                if is_for_executable:
                    update_task_progress(self, node_progress, f"File source (passthrough per executable)...")
                    node_results[node_id] = {
                        "_passthrough_file_source": True,
                        "file_source_id": node_data.get("file_source_id")
                    }
                else:
                    update_task_progress(self, node_progress, f"Caricamento file...")
                    result = load_file_source(node_data)
                    node_results[node_id] = result
            
            elif node_type == "staticValues":
                update_task_progress(self, node_progress, f"Processamento valori statici...")
                result = load_static_values(node_data)
                node_results[node_id] = result
                
            elif node_type == "api_enrich":
                api_id = node_data.get("api_id")
                api_config = api_connections.get(api_id)

                if not api_config:
                    logger.error(
                        f"[GESTITO] [Task {task_id}] API '{api_id}' non trovata nel canvas "                        f"del progetto {project_id}"
                    )
                    return {
                        "success": False,
                        "task_id": task_id,
                        "project_id": project_id,
                        "error": f"Connessione API '{api_id}' non trovata. "                                  "Verifica che la connessione esista e non sia stata eliminata.",
                        "enrichment_stats": enrichment_stats,
                        "error_report": error_report_file,
                    }

                # ── execute_if: skip condizionale ──
                from app.core.arricchisci.parallel import should_execute_node
                _input_edge_check = next((e for e in edges if e["target"] == node_id), None)
                _input_df_check = node_results.get(_input_edge_check["source"]) if _input_edge_check else None
                if not should_execute_node(node_data, _input_df_check):
                    logger.info(f"[Task {task_id}] Nodo {node_id} skippato (execute_if)")
                    update_task_progress(self, node_progress, f"Nodo {node_id} skippato (condizione)")
                    node_results[node_id] = _input_df_check
                    continue

                # ── EXECUTABLE: esegue processore locale (file→file) ──
                if api_config.get("connection_type") == "executable":
                    api_name = api_config.get("name", api_id)
                    update_task_progress(self, node_progress, f"Esecuzione {api_name}...")
                    
                    result = api_connection_service._execute_executable(
                        node_data, api_config, nodes, edges, node_id, project_id
                    )
                    if isinstance(result, dict) and not result.get("success", True):
                        raise ValueError(result.get("error", "Errore esecuzione executable"))
                    
                    node_results[node_id] = result
                else:
                    api_type = api_config.get("api_type", "enricher")
                    api_name = api_config.get("name", api_id)
                
                    if api_type == "generator":
                        update_task_progress(self, node_progress, f"Chiamata API {api_name}...")
                        # Cerca eventuale input (es. da StaticValues)
                        input_edge = next((e for e in edges if e["target"] == node_id), None)
                        input_df = None
                        if input_edge:
                            input_df = node_results.get(input_edge["source"])
                        result = api_connection_service._execute_api_generator(api_config, input_df)
                        node_results[node_id] = result
                    else:
                        # Enricher - elabora con deduplicazione e gestione errori
                        input_edge = next((e for e in edges if e["target"] == node_id), None)
                        
                        # Nodo senza parametri obbligatori mappabili → API autonoma
                        node_parameters = node_data.get("parameters") or api_config.get("parameters") or []
                        needs_input = any(
                            p.get("required") and p.get("mode") != "fixed"
                            for p in node_parameters
                        )
                        
                        if not input_edge and needs_input:
                            raise ValueError(f"Nodo {node_id} non ha input")
                        
                        input_df = node_results.get(input_edge["source"]) if input_edge else None
                        if input_df is None and needs_input:
                            raise ValueError(f"Dati input non trovati per {node_id}")
                        
                        param_mapping = node_data.get("param_mapping", {})
                        
                        # output_mapping: prendi da node_data se presente, altrimenti da api_config
                        # Deduplicazione per colonna (evita duplicati da import OpenAPI)
                        raw_output_mapping = node_data.get("output_mapping") or api_config.get("output_mapping", [])
                        seen_cols = set()
                        deduped = []
                        for m in (raw_output_mapping or []):
                            col = m.get("column") or m.get("path")
                            if col and col not in seen_cols:
                                seen_cols.add(col)
                                deduped.append(m)
                        batch_from_node = node_data.get("batch", {})
                        api_config = {**api_config, "output_mapping": deduped, "batch": batch_from_node or api_config.get("batch", {})}
                        
                        # Opzioni da node_data (configurabili da UI)
                        exec_options = EnrichmentOptions(
                            enable_deduplication=node_data.get("enable_deduplication", True),
                            continue_on_error=node_data.get("continue_on_error", True),
                            stop_on_error_threshold=node_data.get("max_errors", 0) or 0,
                            generate_error_report=node_data.get("generate_error_report", True),
                            skip_null_params=node_data.get("skip_null_params", True),
                            rate_limit=api_config.get("options", {}).get("rate_limit", settings.API_DEFAULT_RATE_LIMIT),
                            request_timeout=api_config.get("options", {}).get("timeout", settings.API_TIMEOUT),
                            max_retries=api_config.get("options", {}).get("retry_max", 3),
                            parallel_workers=api_config.get("options", {}).get("parallel_workers", 1),
                        )
                        
                        # Progress callback che mappa su range del task
                        # IMPORTANTE: usa default argument (_base=node_progress) per catturare
                        # il valore corrente di node_progress, non la reference alla variabile
                        # del loop (che cambierebbe ad ogni iterazione in caso di parallelismo)
                        progress_range = 20
                        def progress_callback(percent: int, message: str, _base=node_progress, _range=progress_range):
                            mapped_progress = _base + int((percent / 100) * _range)
                            update_task_progress(self, mapped_progress, message)
                        
                        # Crea executor ed esegui
                        if input_df is None:
                            # API autonoma (nessun parametro richiesto) → chiamata singola come generator
                            result = api_connection_service._execute_api_generator(api_config, None)
                            enrichment_stats = None
                        else:
                            executor = EnrichmentExecutor(
                                api_config=api_config,
                                param_mapping=param_mapping,
                                options=exec_options,
                                progress_callback=progress_callback
                            )
                            result, enrichment_result = executor.execute(input_df)
                            enrichment_stats = enrichment_result.stats.to_dict()

                            # Se continue_on_error=False e ci sono errori → task fallito
                            if not exec_options.continue_on_error and enrichment_result.stats.errors > 0:
                                logger.error(
                                    f"[GESTITO] [Task {task_id}] Enrichment fallito: "
                                    f"{enrichment_result.stats.errors} errori con continue_on_error=False"
                                )
                                return {
                                    "success": False,
                                    "task_id": task_id,
                                    "project_id": project_id,
                                    "error": (
                                        f"{enrichment_result.stats.errors} righe fallite. "
                                        "Imposta continue_on_error=True per ignorare gli errori."
                                    ),
                                    "enrichment_stats": enrichment_stats,
                                    "error_report": error_report_file,
                                }

                            # Se lo stop_on_error_threshold è stato raggiunto,
                            # il task deve terminare con failure — il loop si è
                            # interrotto prima di processare tutte le righe.
                            threshold = exec_options.stop_on_error_threshold
                            if threshold > 0 and enrichment_result.stats.errors >= threshold:
                                logger.warning(
                                    f"[Task {task_id}] Threshold errori raggiunto "
                                    f"({enrichment_result.stats.errors} >= {threshold}), task fallito"
                                )
                                return {
                                    "success": False,
                                    "task_id": task_id,
                                    "project_id": project_id,
                                    "error": (
                                        f"Superato il limite massimo di errori ({threshold}). "
                                        f"{enrichment_result.stats.errors} righe fallite su "
                                        f"{enrichment_result.stats.total_rows}."
                                    ),
                                    "enrichment_stats": enrichment_stats,
                                    "error_report": error_report_file,
                                }

                            # ── AUTO-SCHEMA: se schema_fields era vuoto (auto-flatten),
                            # salva i tipi rilevati nella config API per il frontend ──
                            if not api_config.get("output_mapping") and executor.detected_column_types:
                                detected = executor.detected_column_types
                                new_schema = [
                                    {"path": col, "name": col, "type": typ}
                                    for col, typ in detected.items()
                                ]
                                # Aggiungi anche le colonne di input sorgente come str
                                for col in (result.columns if hasattr(result, 'columns') else []):
                                    if col not in detected:
                                        new_schema.append({"path": col, "name": col, "type": "str"})
                                try:
                                    api_connection_service.update_api_connection(
                                        api_id, {"schema_fields": new_schema}
                                    )
                                    logger.info(f"[Task {task_id}] Schema auto-rilevato: {list(detected.keys())}")
                                except Exception as _e:
                                    logger.warning(f"[Task {task_id}] Aggiornamento schema fallito: {_e}")
                            if exec_options.generate_error_report and enrichment_result.errors:
                                output_dir = settings.RESULTS_DIR / project_id
                                output_dir.mkdir(parents=True, exist_ok=True)
                                error_report_file = generate_error_report(
                                    enrichment_result.errors,
                                    str(output_dir / f"enrichment_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
                                )
                            logger.info(
                                f"[Task {task_id}] {api_name}: "
                                f"{enrichment_result.stats.success}/{enrichment_result.stats.total_rows} OK, "
                                f"{enrichment_result.stats.cached} cache, "
                                f"{enrichment_result.stats.errors} errori, "
                                f"{enrichment_result.stats.api_calls_saved} chiamate risparmiate"
                            )
                node_results[node_id] = result

                # Snapshot intermediate.db — fire-and-forget thread (non bloccante)
                if _run_id:
                    try:
                        import pandas as _pd
                        _df = result if isinstance(result, _pd.DataFrame) else None
                        if _df is not None and not _df.empty:
                            _rows = _df.to_dict(orient="records")
                            _schema = _df.dtypes.astype(str).to_dict()
                            _nid = node_id
                            _rid = _run_id
                            def _async_write(_r=_rid, _n=_nid, _ro=_rows, _sc=_schema):
                                try:
                                    from app.core.intermediate.writer import write_node as _wn
                                    _wn(run_id=_r, node_id=_n, node_type="api_enrich",
                                        rows=_ro, col_schema=_sc, exec_time=0.0, status="ok")
                                except Exception as _e:
                                    pass  # non bloccante — errori ignorati
                            threading.Thread(target=_async_write, daemon=True).start()
                    except Exception as _sne:
                        logger.warning(f"[Task {task_id}] Intermediate write nodo {node_id} fallita: {_sne}")

            elif node_type == "result":
                input_edge = next((e for e in edges if e["target"] == node_id), None)
                if input_edge:
                    input_df = node_results.get(input_edge["source"])
                    if input_df is not None:
                        node_results[node_id] = input_df
        
        update_task_progress(self, 90, "Salvataggio risultati...")
        
        # Trova output finale
        result_node = next((n for n in nodes if n.get("type") == "result"), None)
        # Merge flat + data (stesso pattern usato per gli altri nodi)
        if result_node:
            result_flat = {k: v for k, v in result_node.items() if k not in ("id", "type", "position", "data", "config")}
            result_data = {**result_flat, **(result_node.get("data") or {})}
        else:
            result_data = {}
        
        final_format = result_data.get("format", output_format) or output_format
        output_filename = result_data.get("fileName", "output") or "output"
        
        final_node_id = execution_order[-1] if execution_order else None
        final_result = node_results.get(final_node_id)
        
        # Cerca anche nel result node
        if final_result is None and result_node:
            final_result = node_results.get(result_node["id"])
        
        # Fallback: ultimo risultato non-passthrough
        if final_result is None:
            for nid in reversed(execution_order):
                r = node_results.get(nid)
                if r is not None and not (isinstance(r, dict) and r.get("_passthrough_file_source")):
                    final_result = r
                    break
        
        if final_result is None:
            raise ValueError("Nessun risultato finale")
        
        # ── EXECUTABLE FILE RESULT ──
        if isinstance(final_result, dict) and final_result.get("is_file_result"):
            from pathlib import Path
            from app.utils.result_store import save_result_blob

            src_path = Path(final_result["output_path"]).resolve()
            elapsed_time = time.time() - start_time
            update_task_progress(self, 100, "Completato!")

            # Rinomina il file con il nome dal nodo result (se specificato)
            src_ext = src_path.suffix  # es. .csv
            if output_filename and output_filename != "output":
                # Assicura che l'estensione sia coerente
                named = Path(output_filename)
                final_name = named.stem + (named.suffix or src_ext)
                renamed_path = src_path.parent / final_name
                src_path.rename(renamed_path)
                src_path = renamed_path

            # B-BE-38: valida output in base all'estensione
            ext = src_path.suffix.lower()
            rows_count = 0
            try:
                if src_path.stat().st_size == 0:
                    raise ValueError("Il file di output è vuoto (0 bytes)")

                if ext == '.csv':
                    import csv as csv_mod
                    raw = src_path.read_bytes()
                    text = raw.decode('utf-8-sig')
                    rows_found = list(csv_mod.reader(text.splitlines()))
                    if len(rows_found) < 2:
                        raise ValueError("CSV vuoto o senza righe dati")
                    rows_count = len(rows_found) - 1

                elif ext in ('.xlsx', '.xls'):
                    import openpyxl
                    wb = openpyxl.load_workbook(str(src_path), read_only=True, data_only=True)
                    ws = wb.active
                    rows_count = max(0, ws.max_row - 1)
                    wb.close()

                elif ext == '.json':
                    import json as json_mod
                    data = json_mod.loads(src_path.read_bytes())
                    rows_count = len(data) if isinstance(data, list) else 1

                elif ext in ('.geojson',):
                    import json as json_mod
                    data = json_mod.loads(src_path.read_bytes())
                    features = data.get('features', []) if isinstance(data, dict) else []
                    rows_count = len(features)

                # Per zip, bin, altri: solo controllo dimensione (già fatto sopra)

            except (UnicodeDecodeError, ValueError, Exception) as val_err:
                src_path.unlink(missing_ok=True)
                raise ValueError(f"Output non valido ({ext}): {val_err}") from val_err

            # Salva nel DB e cancella il file temp (stesso pattern ESTRAI)
            result_id = save_result_blob(
                project_id=project_id,
                path=src_path,
                fmt=src_path.suffix.lstrip(".") or "bin",
                rows_count=rows_count,
            )
            logger.info(f"[Task {task_id}] File result salvato nel DB: {result_id}")
            
            result = {
                "success": True,
                "task_id": task_id,
                "project_id": project_id,
                "output_type": "file",
                "output_file": src_path.name,
                "output_filename": src_path.name,
                
                "rows_processed": 0,
                "columns": [],
                "output_format": src_path.suffix.lstrip("."),
                "elapsed_time": round(elapsed_time, 2),
                "completed_at": datetime.now().isoformat(),
                "execution_time_ms": final_result.get("execution_time_ms", 0),
                "enrichment_stats": None,
                "error_report": None,
                "message": f"Processamento completato: {src_path.name}"
            }
            
            logger.info(f"[Task {task_id}] Executable completato in {elapsed_time:.2f}s - {src_path.name}")

            # ── CARICA anche nel branch executable ──
            try:
                carica_info = _trigger_carica_if_present(
                    project_id=project_id,
                    canvas=canvas,
                    output_format=src_path.suffix.lstrip(".") or "bin",
                )
                if carica_info:
                    result["carica"] = carica_info
            except Exception as _ce:
                logger.error(
                    f"[GESTITO] [Task {task_id}] Errore avvio task CARICA dopo executable: {_ce}",
                    project_id=project_id, exc_info=True
                )

            return result
        
        # ── DATAFRAME RESULT (flusso standard) ──
        final_df = final_result
        
        if final_df is None:
            raise ValueError("Nessun risultato finale")
        # B-BE-59: df vuoto (0 righe) è un risultato valido — non crashare,
        # salva un file con solo l'header.
        if hasattr(final_df, "empty") and final_df.empty:
            logger.info(f"[Task {task_id}] DataFrame vuoto (0 righe) — salvo output con solo header")
        
        # Salva in temp, poi nel DB, poi cancella (stesso pattern ESTRAI)
        from app.utils.dataframe import save_dataframe
        from app.utils.result_store import get_temp_output_path, save_result_blob

        tmp_path = get_temp_output_path(f"{output_filename}.{final_format}")
        saved_path = save_dataframe(df=final_df, output_path=tmp_path, format=final_format)

        elapsed_time = time.time() - start_time

        result_id = save_result_blob(
            project_id=project_id,
            path=saved_path,
            fmt=final_format,
            rows_count=len(final_df),
        )
        logger.info(f"[Task {task_id}] Risultato salvato nel DB: {result_id}")

        # B-BE-39: salva columns_metadata nel progetto per uso come project_source
        try:
            from app.services.project_service import ProjectService as _ProjSvc
            _col_meta = [{"name": col, "type": str(final_df[col].dtype)} for col in final_df.columns]
            _ProjSvc().update_project_columns_metadata(project_id, _col_meta)
            logger.info(f"[Task {task_id}] columns_metadata salvato: {len(_col_meta)} colonne")
        except Exception as _cme:
            logger.warning(f"[Task {task_id}] Impossibile salvare columns_metadata: {_cme}")

        update_task_progress(self, 100, "Completato!")
        
        result = {
            "success": True,
            "task_id": task_id,
            "project_id": project_id,
            "rows_processed": len(final_df),
            "columns": list(final_df.columns),
            "output_file": saved_path.name,
            "output_format": final_format,
            "elapsed_time": round(elapsed_time, 2),
            "completed_at": datetime.now().isoformat(),
            # Statistiche enrichment
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
        }
        
        logger.info(f"[Task {task_id}] Completato in {elapsed_time:.2f}s - {len(final_df)} righe", project_id=project_id)

        # Snapshot result node su intermediate.db — fire-and-forget thread
        if _run_id and result_node:
            try:
                _rows_r = final_df.to_dict(orient="records")
                _schema_r = final_df.dtypes.astype(str).to_dict()
                _nid_r = result_node["id"]
                _rid_r = _run_id
                def _async_write_result(_r=_rid_r, _n=_nid_r, _ro=_rows_r, _sc=_schema_r):
                    try:
                        from app.core.intermediate.writer import write_node as _wn
                        _wn(run_id=_r, node_id=_n, node_type="result",
                            rows=_ro, col_schema=_sc, exec_time=0.0, status="ok")
                    except Exception:
                        pass
                threading.Thread(target=_async_write_result, daemon=True).start()
            except Exception as _sne:
                logger.warning(f"[Task {task_id}] Intermediate write result node fallita: {_sne}")

        if _run_id:
            _exec_time = time.time() - _run_start
            _rid_c = _run_id
            def _async_close(_r=_rid_c, _t=_exec_time):
                try:
                    from app.core.intermediate.writer import close_run as _cr
                    _cr(run_id=_r, input_hash="", output_hash="",
                        total_exec_time=_t, success=True)
                except Exception:
                    pass
            threading.Thread(target=_async_close, daemon=True).start()

        # --- Step tracking + close run su log.db ---
        _elapsed_ms = int((time.time() - _run_start) * 1000)
        _rows_ok = result.get("rows_processed", 0) or 0
        append_step({
            "module":      "arricchisci",
            "status":      "success",
            "rows_out":    _rows_ok,
            "duration_ms": _elapsed_ms,
        })
        close_run(
            run_id=_run_id or task_id,
            status="success",
            rows_out=_rows_ok,
            duration_ms=_elapsed_ms,
        )

        # ── CARICA: lancia task carica se presenti nodi collegati all'output ──
        # Stessa logica di OrchestratorService._execute_carica_if_present ma qui
        # perché execute_enrichment_task non passa per l'orchestrator.
        try:
            carica_info = _trigger_carica_if_present(
                project_id=project_id,
                canvas=canvas,
                output_format=final_format,
            )
            if carica_info:
                # Il frontend legge result.carica.tasks per fare polling sui task carica
                result["carica"] = carica_info
        except Exception as _ce:
            logger.error(
                f"[GESTITO] [Task {task_id}] Errore avvio task CARICA dopo arricchimento: {_ce}",
                project_id=project_id, exc_info=True
            )

        # ── Cleanup risorse temporanee (es. file source dell'eseguibile) ──
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)

        return result

    except Exception as e:
        if _run_id:
            try:
                from app.core.intermediate.writer import close_run as _close_run
                _close_run(run_id=_run_id, input_hash="", output_hash="",
                           total_exec_time=time.time() - _run_start, success=False)
            except Exception:
                pass
        error_message = str(e)
        _elapsed_ms_f = int((time.time() - _run_start) * 1000)
        logger.error(f"[CRASH] [Task {task_id}] Errore: {error_message}", project_id=project_id)

        # --- debug_payload ---
        try:
            from app.utils.debug_payload_builder import DebugPayloadBuilder
            import traceback as _tb
            _debug_payload = DebugPayloadBuilder().build(
                run_id=_run_id or task_id,
                project_id=project_id,
                steps=[],
                failure={
                    "node_id":        None,
                    "node_type":      "api_enrich",
                    "node_label":     "ARRICCHISCI",
                    "module":         "arricchisci",
                    "error_type":     type(e).__name__,
                    "error_message":  error_message,
                    "traceback":      _tb.format_exc(),
                    "occurred_at_ms": _elapsed_ms_f,
                },
            )
        except Exception:
            _debug_payload = None

        close_run(
            run_id=_run_id or task_id,
            status="failure",
            duration_ms=_elapsed_ms_f,
            error_module="arricchisci",
            error_message=error_message,
            debug_payload=_debug_payload,
        )
        
        # NON usare state="FAILURE" direttamente - causa problemi con Celery/Redis
        # Invece, aggiorniamo con progress negativo ma stato PROGRESS e is_error=True
        try:
            update_task_progress(self, 0, f"Errore: {error_message}", is_error=True)
        except Exception:
            pass  # Ignora errori di update

        # ── Cleanup risorse temporanee anche in caso di errore ──
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)

        # Ritorna dict con errore (serializzabile)
        return {
            "success": False,
            "task_id": task_id,
            "project_id": project_id,
            "error": error_message,
            "failed_at": datetime.now().isoformat(),
            # Statistiche parziali se disponibili
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
        }


# =============================================================================
# FUNZIONI HELPER PER IL TASK
# =============================================================================


def _cleanup_temp_file_sources(file_source_ids: Optional[List[str]], task_id: str) -> None:
    """Elimina i file source temporanei creati per l'esecuzione (es. eseguibili).

    Non solleva mai eccezioni: in caso di errore logga e prosegue.
    """
    if not file_source_ids:
        return
    try:
        from app.services.file_service import file_source_service
    except Exception as e:
        logger.warning(f"[Task {task_id}] Cleanup: import file_source_service fallito: {e}")
        return

    for fsid in file_source_ids:
        try:
            ok = file_source_service.delete_file_source(fsid)
            if ok:
                logger.info(f"[Task {task_id}] Cleanup: file source temp '{fsid}' eliminato")
            else:
                logger.warning(f"[Task {task_id}] Cleanup: file source temp '{fsid}' non trovato")
        except Exception as e:
            logger.warning(f"[Task {task_id}] Cleanup: errore eliminando '{fsid}': {e}")


def update_task_progress(task, progress: int, message: str, is_error: bool = False):
    """
    Aggiorna il progresso del task in Redis.

    Usa sempre lo stato 'PROGRESS' (mai 'FAILURE') per evitare che Celery
    interpreti il task come terminato. Il frontend determina lo stato
    reale dal flag is_error e dall'eventuale prefisso "Errore:" nel messaggio.
    """
    task.update_state(
        state="PROGRESS",
        meta={
            "progress": max(0, progress),
            "message": message,
            "is_error": is_error,
            "updated_at": datetime.now().isoformat()
        }
    )


# ── Funzioni delegate ai moduli app.* ────────────────────────────────────────
# Il worker Celery importa normalmente da app.* — non serve duplicare.
# Usando le versioni canoniche si garantisce che fix e miglioramenti
# si propaghino automaticamente senza aggiornare il worker separatamente.

def get_execution_order(nodes: list, edges: list) -> list:
    """Delega a api_connector._get_execution_order (algoritmo di Kahn)."""
    from app.core.arricchisci.api_connector import api_connection_service
    return api_connection_service._get_execution_order(nodes, edges)


def find_latest_output_file(output_dir):
    """Delega a app.utils.dataframe.find_latest_output (cerca file più recente)."""
    from app.utils.dataframe import find_latest_output
    return find_latest_output(str(output_dir))


def load_dataframe_from_file(file_path):
    """Delega a app.utils.dataframe.load_dataframe_from_file."""
    from app.utils.dataframe import load_dataframe_from_file as _load
    return _load(str(file_path))


def load_source_project(node_data: Dict, force_recalculate: bool = False):
    """Delega a api_connector._load_source_project."""
    from app.core.arricchisci.api_connector import api_connection_service
    return api_connection_service._load_source_project(node_data, force_recalculate)


def load_file_source(node_data: Dict):
    """Delega a api_connector._load_file_source."""
    from app.core.arricchisci.api_connector import api_connection_service
    return api_connection_service._load_file_source(node_data)


def load_static_values(node_data: Dict):
    """Delega a api_connector._load_static_values."""
    from app.core.arricchisci.api_connector import api_connection_service
    return api_connection_service._load_static_values(node_data)


def _trigger_carica_if_present(project_id: str, canvas: Dict, output_format: str = "csv") -> Optional[Dict[str, Any]]:
    """Cerca nodi CARICA (db/arcgis/datasource) collegati al nodo Output e lancia i relativi task.

    Replica la logica di OrchestratorService._execute_carica_if_present, ma viene chiamata
    direttamente dal task di enrichment perché /enrich-async non passa per l'orchestrator.

    Returns: dict {"tasks": [{task_id, node_id}, ...], "message": ...} oppure None se
             nessun nodo carica trovato. Il frontend usa questo per fare polling sui task.
    """
    nodes = canvas.get("nodes", []) or []
    edges = canvas.get("edges", []) or []

    # Trova nodo Output (result)
    output_node_id = next((n["id"] for n in nodes if n.get("type") == "result"), None)
    if not output_node_id:
        return None

    # Cerca nodi carica/carica_arcgis/datasource_node collegati all'output
    carica_nodes = []
    for node in nodes:
        ntype = node.get("type")
        if ntype in ("carica", "carica_arcgis", "datasource_node"):
            connected = any(
                e.get("source") == output_node_id and e.get("target") == node.get("id")
                for e in edges
            )
            if connected:
                carica_nodes.append(node)
            else:
                logger.warning(
                    f"Nodo {ntype} ({node.get('id')}) non collegato al nodo Output, skip"
                )

    if not carica_nodes:
        return None

    logger.info(
        f"Trovati {len(carica_nodes)} nodi CARICA dopo arricchimento, avvio task",
        project_id=project_id
    )

    from app.services.task_service import task_service
    from app.services.project_service import ProjectService
    try:
        metadata = ProjectService().get_project_metadata(project_id)
        group_id = getattr(metadata, 'group_id', None) if metadata else None
    except Exception:
        group_id = None

    tasks = []
    for carica_node in carica_nodes:
        try:
            # Merge node root + node['data'] (stesso pattern dell'orchestratore)
            node_dict = dict(carica_node)
            nested_data = node_dict.get("data") or {}
            carica_node_data = {**node_dict, **nested_data}

            task_info = task_service.create_carica_task(
                project_id=project_id,
                canvas=canvas,
                carica_node_data=carica_node_data,
                output_format=output_format,
                triggered_by="manual",
            )
            logger.info(
                f"Task CARICA avviato: {task_info.task_id} (nodo: {carica_node.get('id')})",
                project_id=project_id
            )
            tasks.append({"task_id": task_info.task_id, "node_id": carica_node.get("id")})
        except Exception as e:
            logger.error(
                f"[GESTITO] Errore creazione task CARICA per nodo {carica_node.get('id')}: {e}",
                project_id=project_id, exc_info=True
            )

    if not tasks:
        return None

    return {
        "tasks": tasks,
        "message": f"{len(tasks)} task CARICA avviati dopo arricchimento",
    }
