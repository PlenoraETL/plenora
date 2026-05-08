"""
Task Celery per arricchimento dati asincrono.

Runs background execution while preserving backend runtime contracts.
"""

import csv as csv_mod
import json
import json as json_mod
import os
import shutil as _shutil
import tempfile as _tf
import threading
import time
import traceback as _tb
import uuid as _uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List
from celery import current_task
from workers.celery_app import celery_app
from app.config import settings
from app.utils.logger import get_logger, open_run, close_run, set_run_context, append_step
from app.config import settings
from app.core.arricchisci.enrichment_service import EnrichmentExecutor, generate_error_report
from app.core.arricchisci.models import EnrichmentOptions, EnrichmentResult

logger = get_logger(__name__)


def _merge_batch_parallel_node_results(
    common_input_df: object,
    node_results: Dict[str, Any],
    node_ids: List[str],
    task_id: str,
) -> None:
    """Merge dei DataFrame prodotti da un batch parallel di nodi enrichment.

    Invariante: ogni nodo del ritornare un DataFrame della stessa
    cardinalita' del `common_input_df`. Se un nodo riduce o aumenta le righe
    (es. esplosione FeatureCollection in via sequenziale, dedup interna),
    il merge column-by-column non e' applicabile e produrrebbe corruption
    silenziosa.

    RB.#14 (2026-04-27): pre-fix il merge era avvolto da try/except generico
    che catturava il ValueError pandas (`Length of values (X) does not match
    length of index (Y)`), emetteva warning, e proseguiva senza le colonne
    arricchite del nodo problematico. Il task chiudeva success=True con
    `rows_processed=0` (PDF generato senza arricchimenti). Post-fix il guard
    fail-hard solleva RuntimeError diagnostico -> global handler chiude
    `close_run(success=False)`. Meglio task fallito visibile che success
    silente con dati persi.

    Args:
        common_input_df: DataFrame di input condiviso dai nodi del batch.
        node_results: dict {node_id: DataFrame|other} con i risultati dei nodi.
        node_ids: lista degli id dei nodi del mergiare.
        task_id: id del task Celery, usato nel messaggio diagnostico.

    Side effects:
        - Sovrascrive `node_results[nid]` per ogni `nid` in `node_ids` con il
          DataFrame merged, cosi' i nodi a valle nel DAG leggono colonne complete.

    Raises:
        RuntimeError: se un node_result ha cardinalita' diversa da
            `common_input_df`. Il messaggio cita task_id, node_id,
            len(result_df), len(common_input_df), e cause probabili.
    """
    import pandas as _pd

    merged = common_input_df.copy() if hasattr(common_input_df, "copy") else common_input_df
    expected_len = len(merged)
    for nid in node_ids:
        nr = node_results.get(nid)
        if not isinstance(nr, _pd.DataFrame):
            continue
        if len(nr) != expected_len:
            raise RuntimeError(
                f"[Task {task_id}] Batch parallel merge mismatch su "
                f"nodo '{nid}': result_df ha {len(nr)} righe vs "
                f"common_input_df {expected_len}. Cause probabili: "
                f"esplosione FeatureCollection (multi-feature response "
                f"in via sequenziale `_execute_internal`) o deduplica "
                f"interna che riduce le righe. Il merge column-by-column "
                f"produrrebbe corruption silenziosa o perdita dati. "
                f"Task fallito esplicitamente."
            )
        new_cols = [c for c in nr.columns if c not in merged.columns]
        for c in new_cols:
            merged[c] = nr[c].values
    for nid in node_ids:
        node_results[nid] = merged


@celery_app.task(bind=True, name="tasks.execute_enrichment")
def execute_enrichment_task(
    self,
    project_id: str,
    canvas: Dict,
    output_format: str = "csv",
    force_recalculate: bool = False,
    cleanup_file_source_ids: Optional[List[str]] = None,
    is_eseguibile: bool = False,
    run_id: Optional[str] = None,
    user_id: Optional[str] = None,
    intermediate_mode_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Task asincrono per eseguire l'arricchimento dati.

    Idempotency (C4 round 3): se `run_id` viene passato dall'esterno
    (scheduler, retry-handler UI) e in intermediate.runs esiste gia'
    un record con status='completed' per quel run_id, il task SKIPPA
    l'esecuzione e ritorna un risultato cached. Stesso pattern del
    carica_task. Se run_id non viene passato → genera fresh (behavior
    legacy, no idempotency).

    Args:
        project_id: ID del progetto
        canvas: Canvas con nodi e edges
        output_format: Formato output (csv, xlsx, geojson, etc.)
        force_recalculate: Se True, riesegue anche se esiste cache
        cleanup_file_source_ids: Lista di file_source_id da eliminare al termine
            del task (sia in caso di successo che di errore). Usato dagli eseguibili
            per rimuovere i file temporanei creati durante l'esecuzione on-demand.
        is_eseguibile: True quando il task gira per un "eseguibile" pubblicato.
            In questa modalita' il task NON scrive nulla su DB (runs, log_runs,
            results, node_rows, columns_metadata): persiste l'output su disco in
            ESEGUIBILI_OUTPUTS_DIR/{task_id}/ e il frontend lo scarica via
            GET /api/eseguibili/{id}/download/{task_id}, che elimina il file
            dopo l'invio. Un cleanup periodico (celery beat) rimuove eventuali
            file orfani.
        run_id: Chiave di idempotency opzionale. Se passato e gia'
            completato in intermediate.runs → task skippato.

    Returns:
        Dict con risultato dell'esecuzione, oppure status='skipped_idempotent'
        se il run_id era gia' completo.
    """
    task_id = self.request.id

    # C4 round 3: idempotency guard. Se run_id e' passato dall'esterno
    # scheduler con schedule_run_id, UI retry-handler) ed e' gia'
    # completato in intermediate.runs, skip senza ri-eseguire
    if run_id is not None:
        from app.shared.idempotency import is_already_completed
        if is_already_completed(run_id):
            logger.info(
                f"[IDEMPOTENCY] Task ENRICHMENT {task_id}: run_id {run_id} "
                f"gia' completato con successo, skip."
            )
            return {
                "status": "skipped_idempotent",
                "success": True,
                "message": f"Run {run_id} gia' completato",
                "run_id": run_id,
                "task_id": task_id,
            }

    start_time = time.time()

    # N9: re-check permessi user al pickup (helper condiviso
    # Per is_eseguibile, skip: gli eseguibili hanno flusso di auth
    # separato (token eseguibile) e non passano per user_id interattivo
    if not is_eseguibile:
        from app.shared.async_user_recheck import recheck_user_authorized
        _revoked = recheck_user_authorized(
            user_id=user_id, project_id=project_id,
            task_id=task_id, run_id=run_id or "", task_name="ENRICHMENT",
        )
        if _revoked is not None:
            _revoked.update({"task_id": task_id, "project_id": project_id})
            return _revoked

    # Per gli eseguibili non propaghiamo project_id ai logger: il PostgresLogHandler
    # lo userebbe come colonna `logs.project_id` creando tracce persistenti che
    # vogliamo evitare
    _log_pid = "" if is_eseguibile else project_id

    logger.info(f"[Task {task_id}] Avvio enrichment per progetto {project_id}", project_id=_log_pid)

    # Aggiorna stato: STARTED
    update_task_progress(self, 0, "Inizializzazione...", task_id=task_id)

    # Variabili per statistiche enrichment
    enrichment_stats = None
    error_report_file = None

    # Apre run in intermediate.db (non bloccante
    # Per gli eseguibili saltiamo `intermediate.create_run` perche' l'eseguibile
    # non e' un progetto in `projects`, quindi un record runs con
    # canvas_id="exec_..." sarebbe orfano. MA il `_group_id` deve comunque
    # essere recuperato (dalla tabella `eseguibili`) per propagarlo al
    # tenant binding di `api_connection_service` piu' sotto: senza
    # `_load_*` / `_execute_rest_file` falliscono fail-closed con
    # "Contesto di sicurezza non determinabile (group_id mancante)"
    _run_start = time.time()
    _group_id = ""
    _retention_n = None
    if is_eseguibile:
        _run_id = None
        try:
            from app.core.eseguibili.db import get_eseguibile as _get_eseg
            _eseg = _get_eseg(project_id)
            _group_id = (_eseg.get("group_id") or "") if _eseg else ""
        except Exception as _ge:
            logger.warning(
                f"[Task {task_id}] Eseguibile group_id lookup fallito "
                f"(non bloccante): {_ge}"
            )
    else:
        _run_id = str(_uuid.uuid4())
        try:
            from app.core.intermediate import db as _idb
            from app.core.projects.service import ProjectService as _PS
            from app.core.projects.retention import resolve_runs_retention_n
            _meta = _PS().get_project_metadata(project_id)
            _group_id = getattr(_meta, 'group_id', 'unknown') if _meta else 'unknown'
            _retention_n = resolve_runs_retention_n(
                project_id,
                default=_idb.DEFAULT_RUNS_RETENTION,
            )
            _idb.create_run(run_id=_run_id, canvas_id=project_id, group_id=_group_id, user_id=user_id)
        except Exception as _rie:
            logger.warning(f"[Task {task_id}] Intermediate: apertura run fallita (non bloccante): {_rie}")
            _run_id = None

    # Per eseguibili: niente log_runs/logs su DB
    if not is_eseguibile:
        open_run(
            run_id=_run_id or task_id,
            project_id=project_id,
            group_id=_group_id,
            module="arricchisci",
            triggered_by="manual",
            user_id=user_id,
        )
        set_run_context(run_id=_run_id or task_id, project_id=project_id)

    try:
        # Import qui per evitare circular imports
        # NOTE: istanziamo una NUOVA `ApiConnectionService` invece di
        # usare il singleton modulo-level. Il singleton ha stato per-execution
        # che e' soggetto a race condition cross-tenant fra task paralleli
        # sullo stesso worker process (Celery prefork riusa i fork
        # Un'istanza locale per task garantisce isolamento totale del tenant
        # binding
        from app.core.arricchisci.api_connector import ApiConnectionService
        api_connection_service = ApiConnectionService()
        # — costruisci un
        # `ArricchisciExecutionContext` tipato esplicito; il context
        # viene threadato esplicitamente ai 4 mixin callsite
        # `_load_source_project`, `_load_file_source`
        # `_load_static_values`, `_execute_rest_file`) tramite `ctx=ctx`
        # + OE012: il worker NON
        # muta `svc._current_group_id` / `svc._preview_limit`. I 4
        # mixin reader site sono ctx-aware da TE-019/-020/-024/-026: il
        # ctx tipato e' l'unica via di tenant binding nel path del
        # worker. Post-OE012 la funzione legacy `apply_to_legacy_state`
        # `tests2/arricchisci/unit/test_external_consumers_ctx_migration_contract.py`
        # protegge contro re-introduzione
        from app.core.arricchisci.execution_context import (
            ArricchisciExecutionContext,
        )
        ctx = ArricchisciExecutionContext(
            project_id=project_id,
            group_id=(
                _group_id if _group_id and _group_id != 'unknown' else None
            ),
            preview_limit=None,  # task celery non e' preview
        )

        nodes = canvas.get("nodes", [])
        edges = canvas.get("edges", [])

        if not nodes:
            raise ValueError("Nessun nodo nel canvas")

        update_task_progress(self, 5, "Caricamento dati...", task_id=task_id)

        # Carica API connections
        api_connections = {}
        for api in api_connection_service.list_api_connections():
            full_api = api_connection_service.get_api_connection(api["id"], include_token=True)
            if full_api:
                api_connections[api["id"]] = full_api
        
        update_task_progress(self, 10, "API caricate, inizio elaborazione...", task_id=task_id)

        from app.core.arricchisci.canvas_orchestrator import (
            get_execution_order,
        )
        execution_order = get_execution_order(nodes, edges)
        node_results = {}
        
        # compute_parallel_groups raggruppa api_enrich consecutivi che non
        # dipendono l'uno dall'output dell'altro (leggono solo colonne gia'
        # presenti nel DataFrame al momento del gruppo) → possono girare
        # concorrentemente. I nodi di tipo source/static/result restano
        # "single" (eseguiti uno alla volta
        from app.core.arricchisci.parallel import compute_parallel_groups, should_execute_node

        parallel_groups = compute_parallel_groups(
            execution_order, nodes, edges,
            api_connections=api_connections,
        )
        logger.info(
            f"[Task {task_id}] Gruppi esecuzione: "
            f"{[(t, len(ids)) for t, ids in parallel_groups]}"
        )

        class _EarlyTaskReturn(Exception):
            """Usata dentro la closure per early termination con payload.

            Il loop esterno cattura questa eccezione e propaga il payload
            come return del task. E' l'equivalente async-safe del `return`
            diretto che il vecchio loop faceva in linea.
            """
            def __init__(self, payload: Dict[str, Any]) -> None:
                self.payload = payload

        def _run_single_node(node_id: str, node_progress: int,
                             input_override: object = None) -> None:
            """Esegue un singolo nodo del canvas.

            Chiamata sequenzialmente per gli step "single" o in parallelo
            (via ThreadPoolExecutor) per gli step "parallel" con >1 nodi
            api_enrich indipendenti. Modifica node_results[node_id] in-place;
            side-effect su enrichment_stats / error_report_file via nonlocal.

            Parametri:
                input_override: se non None, il nodo api_enrich lo usa come
                    input_df invece di leggere node_results[input_edge.source].
                    Serve nel caso dei dove tutti i nodi del
                    lo stesso input (il predecessore comune
                    del batch), anche se topologicamente nel canvas sono
                    concatenati (pattern pass-through).

            Early termination: solleva _EarlyTaskReturn(payload) per errori
            bloccanti (API mancante, continue_on_error=False con errori,
            threshold errori raggiunto). Il caller propaga al global handler.
            """
            nonlocal enrichment_stats, error_report_file

            node = next((n for n in nodes if n["id"] == node_id), None)
            if not node:
                return

            node_type = node.get("type")
            # Merge: campi flat del nodo + node['data'] (priorita' ai campi flat? no
            # a data — vedi commento vecchio codice). Stesso comportamento del loop
            # originale
            node_flat = {k: v for k, v in node.items() if k not in ("id", "type", "position", "data", "config")}
            node_data = {**node_flat, **(node.get("data") or {})}

            if node_type == "project_source":
                update_task_progress(self, node_progress, f"Caricamento sorgente dati...", task_id=task_id)
                result = api_connection_service._load_source_project(
                    node_data, force_recalculate, ctx=ctx,
                )
                node_results[node_id] = result
                return

            if node_type == "file_source":
                # Se il downstream e' un file-processor (rest_file) il file
                # non va letto come DataFrame: resta un marker passthrough
                # e il file vero viene risolto dal processor via file_source_id
                downstream_edges = [e for e in edges if e["source"] == node_id]
                is_for_file_processor = False
                for de in downstream_edges:
                    target_node = next((n for n in nodes if n["id"] == de["target"]), None)
                    if target_node and target_node.get("type") == "api_enrich":
                        # Pydantic Node serializza tutti i campi al root del nodo
                        # `data` resta vuoto. Cerco prima al root, fallback dentro data
                        target_api_id = target_node.get("api_id") or (target_node.get("data") or {}).get("api_id")
                        target_api_config = api_connections.get(target_api_id, {})
                        if target_api_config.get("connection_type") == "rest_file":
                            is_for_file_processor = True
                            break

                if is_for_file_processor:
                    update_task_progress(self, node_progress, f"File source (passthrough per file processor)...", task_id=task_id)
                    node_results[node_id] = {
                        "_passthrough_file_source": True,
                        "file_source_id": node_data.get("file_source_id")
                    }
                else:
                    update_task_progress(self, node_progress, f"Caricamento file...", task_id=task_id)
                    result = api_connection_service._load_file_source(
                        node_data, ctx=ctx,
                    )
                    node_results[node_id] = result
                return

            if node_type == "staticValues":
                update_task_progress(self, node_progress, f"Processamento valori statici...", task_id=task_id)
                result = api_connection_service._load_static_values(
                    node_data, ctx=ctx,
                )
                node_results[node_id] = result
                return

            if node_type == "result":
                # Il nodo result non produce output: prende l'input dall'edge
                # il salvataggio del risultato finale avviene dopo il loop
                input_edge = next((e for e in edges if e["target"] == node_id), None)
                if input_edge:
                    input_df = node_results.get(input_edge["source"])
                    if input_df is not None:
                        node_results[node_id] = input_df
                return

            if node_type == "imdf_publisher":
                # Passthrough nel DAG. La pubblicazione vera su ArcGIS avviene
                # nel blocco post-processing api_enrich (dove il bundle IMDF e'
                # gia' estratto dallo zip). Qui propaghiamo solo il risultato
                # del predecessore perche' non romperemo l'execution order e
                # perche' cosi' un eventuale result downstream riceve l'input
                input_edge = next((e for e in edges if e["target"] == node_id), None)
                if input_edge:
                    node_results[node_id] = node_results.get(input_edge["source"])
                return

            if node_type != "api_enrich":
                return  # tipo sconosciuto → skip silente (era il comportamento del vecchio loop

            api_id = node_data.get("api_id")
            api_config = api_connections.get(api_id)

            if not api_config:
                logger.error(
                    f"[GESTITO] [Task {task_id}] API '{api_id}' non trovata nel canvas "
                    f"del progetto {project_id}"
                )
                raise _EarlyTaskReturn({
                    "success": False,
                    "task_id": task_id,
                    "project_id": project_id,
                    "error": f"Connessione API '{api_id}' non trovata. "
                             "Verifica che la connessione esista e non sia stata eliminata.",
                    "enrichment_stats": enrichment_stats,
                    "error_report": error_report_file,
                })

            # ── execute_if: skip condizionale ──
            _input_edge_check = next((e for e in edges if e["target"] == node_id), None)
            _input_df_check = node_results.get(_input_edge_check["source"]) if _input_edge_check else None
            if not should_execute_node(node_data, _input_df_check):
                logger.info(f"[Task {task_id}] Nodo {node_id} skippato (execute_if)")
                update_task_progress(self, node_progress, f"Nodo {node_id} skippato (condizione)", task_id=task_id)
                node_results[node_id] = _input_df_check
                return

            # ── REST FILE: invia file a servizio HTTP remoto, riceve binario ──
            if api_config.get("connection_type") == "rest_file":
                api_name = api_config.get("name", api_id)
                update_task_progress(self, node_progress, f"Esecuzione {api_name} (REST file)...", task_id=task_id)

                # threadato per il rest_file pipeline (group_id letto
                # da `ctx` invece di singleton state
                result = api_connection_service._execute_rest_file(
                    node_data, api_config, nodes, edges, node_id, project_id,
                    node_results=node_results, ctx=ctx,
                )
                if isinstance(result, dict) and not result.get("success", True):
                    raise ValueError(result.get("error", "Errore esecuzione REST file processor"))

                node_results[node_id] = result

            else:
                api_type = api_config.get("api_type", "enricher")
                api_name = api_config.get("name", api_id)

                if api_type == "generator":
                    update_task_progress(self, node_progress, f"Chiamata API {api_name}...", task_id=task_id)
                    input_edge = next((e for e in edges if e["target"] == node_id), None)
                    input_df = None
                    if input_edge:
                        input_df = node_results.get(input_edge["source"])
                    result = api_connection_service._execute_api_generator(api_config, input_df)
                    node_results[node_id] = result
                else:
                    # Enricher
                    input_edge = next((e for e in edges if e["target"] == node_id), None)

                    node_parameters = node_data.get("parameters") or api_config.get("parameters") or []
                    needs_input = any(
                        p.get("required") and p.get("mode") != "fixed"
                        for p in node_parameters
                    )

                    if not input_edge and needs_input and input_override is None:
                        raise ValueError(f"Nodo {node_id} non ha input")

                    # Se il chiamante ha fornito un input_override (esecuzione
                    # in ), lo usiamo come input del nodo
                    # invece di leggere da node_results[input_edge.source
                    # Questo e' il meccanismo con cui i nodi di un batch
                    # condividono lo stesso DataFrame di partenza anche quando
                    # topologicamente nel canvas sono concatenati
                    if input_override is not None:
                        input_df = input_override
                    else:
                        input_df = node_results.get(input_edge["source"]) if input_edge else None
                    if input_df is None and needs_input:
                        raise ValueError(f"Dati input non trovati per {node_id}")

                    param_mapping = node_data.get("param_mapping", {})

                    raw_output_mapping = node_data.get("output_mapping") or api_config.get("output_mapping", [])
                    seen_cols = set()
                    deduped = []
                    for m in (raw_output_mapping or []):
                        col = m.get("column") or m.get("path")
                        if col and col not in seen_cols:
                            seen_cols.add(col)
                            deduped.append(m)
                    batch_from_node = node_data.get("batch", {})
                    # NOTE: non riassegnare api_config (variabile closure condivisa
                    # tra thread). Usa una copia locale api_config_local
                    api_config_local = {
                        **api_config,
                        "output_mapping": deduped,
                        "batch": batch_from_node or api_config.get("batch", {}),
                    }

                    exec_options = EnrichmentOptions(
                        enable_deduplication=node_data.get("enable_deduplication", True),
                        continue_on_error=node_data.get("continue_on_error", True),
                        stop_on_error_threshold=node_data.get("max_errors", 0) or 0,
                        generate_error_report=node_data.get("generate_error_report", True),
                        skip_null_params=node_data.get("skip_null_params", True),
                        rate_limit=api_config_local.get("options", {}).get("rate_limit", settings.API_DEFAULT_RATE_LIMIT),
                        request_timeout=api_config_local.get("options", {}).get("timeout", settings.API_TIMEOUT),
                        max_retries=api_config_local.get("options", {}).get("retry_max", 3),
                        parallel_workers=api_config_local.get("options", {}).get("parallel_workers", 1),
                    )

                    # Progress callback: usa default argument per catturare node_progress
                    # al momento della creazione (non riferimento mutabile
                    progress_range = 20
                    def progress_callback(
                        percent: int,
                        message: str,
                        _base: int = node_progress,
                        _range: int = progress_range,
                    ) -> None:
                        mapped_progress = _base + int((percent / 100) * _range)
                        update_task_progress(self, mapped_progress, message, task_id=task_id)

                    if input_df is None:
                        # API autonoma (nessun parametro richiesto) → chiamata singola come generator
                        result = api_connection_service._execute_api_generator(api_config_local, None)
                        enrichment_stats = None
                    else:
                        executor = EnrichmentExecutor(
                            api_config=api_config_local,
                            param_mapping=param_mapping,
                            options=exec_options,
                            progress_callback=progress_callback,
                        )
                        result, enrichment_result = executor.execute(input_df)
                        enrichment_stats = enrichment_result.stats.to_dict()

                        if not exec_options.continue_on_error and enrichment_result.stats.errors > 0:
                            logger.error(
                                f"[GESTITO] [Task {task_id}] Enrichment fallito: "
                                f"{enrichment_result.stats.errors} errori con continue_on_error=False"
                            )
                            raise _EarlyTaskReturn({
                                "success": False,
                                "task_id": task_id,
                                "project_id": project_id,
                                "error": (
                                    f"{enrichment_result.stats.errors} righe fallite. "
                                    "Imposta continue_on_error=True per ignorare gli errori."
                                ),
                                "enrichment_stats": enrichment_stats,
                                "error_report": error_report_file,
                            })

                        threshold = exec_options.stop_on_error_threshold
                        if threshold > 0 and enrichment_result.stats.errors >= threshold:
                            logger.warning(
                                f"[Task {task_id}] Threshold errori raggiunto "
                                f"({enrichment_result.stats.errors} >= {threshold}), task fallito"
                            )
                            raise _EarlyTaskReturn({
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
                            })

                        # ── AUTO-SCHEMA ──
                        if not api_config_local.get("output_mapping") and executor.detected_column_types:
                            detected = executor.detected_column_types
                            new_schema = [
                                {"path": col, "name": col, "type": typ}
                                for col, typ in detected.items()
                            ]
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
                                str(output_dir / f"enrichment_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"),
                                auth=api_config_local.get("auth"),
                            )
                        logger.info(
                            f"[Task {task_id}] {api_name}: "
                            f"{enrichment_result.stats.success}/{enrichment_result.stats.total_rows} OK, "
                            f"{enrichment_result.stats.cached} cache, "
                            f"{enrichment_result.stats.errors} errori, "
                            f"{enrichment_result.stats.api_calls_saved} chiamate risparmiate"
                        )
            node_results[node_id] = result

            if _run_id:
                try:
                    import pandas as _pd
                    _df = result if isinstance(result, _pd.DataFrame) else None
                    if _df is not None and not _df.empty:
                        from app.core.intermediate.snapshot import (
                            record_checkpoint_snapshot,
                        )
                        record_checkpoint_snapshot(
                            run_id=_run_id,
                            module="arricchisci",
                            node_id=node_id,
                            node_type="api_enrich",
                            df=_df,
                            exec_time=0.0,
                            status="ok",
                            mode_override=intermediate_mode_override,
                        )
                except Exception as _sne:
                    logger.warning(f"[Task {task_id}] Intermediate write nodo {node_id} fallita: {_sne}")

        for step_type, node_ids in parallel_groups:
            # Progress base del gruppo = progress del primo nodo nel execution_order
            idx_first = execution_order.index(node_ids[0])
            base_progress = 10 + int((idx_first / len(execution_order)) * 80)

            if len(node_ids) == 1:
                # Single: niente overhead threading, lettura normale via input_edge
                _run_single_node(node_ids[0], base_progress)
                continue

            # Parallel: N nodi api_enrich indipendenti
            # Modello "fan-out artificiale": anche se topologicamente nel
            # canvas i nodi del essere concatenati (es
            # A→B→C pass-through), logicamente consumano colonne gia'
            # prodotte *prima* del aggiungono colonne disgiunte
            # Per eseguirli in parallelo devono tutti vedere lo stesso
            # input_df: quello del predecessore del PRIMO nodo del batch
            first_node_id = node_ids[0]
            first_input_edge = next(
                (e for e in edges if e["target"] == first_node_id), None
            )
            common_input_df = None
            if first_input_edge:
                common_input_df = node_results.get(first_input_edge["source"])
            if common_input_df is None:
                # Fallback difensivo: se per qualche motivo non troviamo
                # l'input comune, ripiega su esecuzione sequenziale (il
                # vecchio comportamento, che usa input_edge per-nodo
                logger.warning(
                    f"[Task {task_id}] Batch parallelo senza input comune, "
                    f"fallback a esecuzione sequenziale per {len(node_ids)} nodi"
                )
                for nid in node_ids:
                    _run_single_node(nid, base_progress)
                continue

            max_workers = min(len(node_ids), 10)
            logger.info(
                f"[Task {task_id}] Esecuzione parallela: {len(node_ids)} nodi "
                f"(workers={max_workers}, input_rows={len(common_input_df)})"
            )
            update_task_progress(
                self, base_progress,
                f"Esecuzione parallela di {len(node_ids)} nodi...",
                task_id=task_id,
            )
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = {
                    pool.submit(
                        _run_single_node, nid, base_progress, common_input_df
                    ): nid
                    for nid in node_ids
                }
                for future in as_completed(futures):
                    # future.result risolleva eventuali _EarlyTaskReturn o
                    # altre eccezioni → propagate al global handler del task
                    future.result()

            # Merge helper module-level con guard fail-hard
            # su cardinality mismatch (RB.#14). Side-effect: sovrascrive
            # node_results[nid] di ogni nodo del il merged DataFrame
            # cosi' i nodi a valle nel DAG leggono tutte le colonne. Solleva
            # RuntimeError se un nodo ritorna len diverso da common_input_df
            # bubble-up al global handler -> close_run(success=False
            _merge_batch_parallel_node_results(
                common_input_df=common_input_df,
                node_results=node_results,
                node_ids=node_ids,
                task_id=task_id,
            )

        update_task_progress(self, 90, "Salvataggio risultati...", task_id=task_id)
        
        # Trova output finale
        result_node = next((n for n in nodes if n.get("type") == "result"), None)
        # Merge flat + data (stesso pattern usato per gli altri nodi
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

            src_path = Path(final_result["output_path"]).resolve()
            elapsed_time = time.time() - start_time
            update_task_progress(self, 100, "Completato!", task_id=task_id)

            # Rinomina il file con il nome dal nodo result (se specificato
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
                    raw = src_path.read_bytes()
                    text = raw.decode('utf-8-sig')
                    rows_found = list(csv_mod.reader(text.splitlines()))
                    if len(rows_found) < 2:
                        raise ValueError("CSV vuoto o senza righe dati")
                    rows_count = len(rows_found) - 1

                elif ext in ('.xlsx', '.xls'):
                    import openpyxl
                    wb = openpyxl.load_workbook(str(src_path), read_only=True, data_only=True)
                    try:
                        ws = wb.active
                        rows_count = max(0, ws.max_row - 1)
                    finally:
                        wb.close()

                elif ext == '.json':
                    data = json_mod.loads(src_path.read_bytes())
                    rows_count = len(data) if isinstance(data, list) else 1

                elif ext in ('.geojson',):
                    data = json_mod.loads(src_path.read_bytes())
                    features = data.get('features', []) if isinstance(data, dict) else []
                    rows_count = len(features)

                # Per zip, bin, altri: solo controllo dimensione (già fatto sopra

            except (UnicodeDecodeError, ValueError, Exception) as val_err:
                src_path.unlink(missing_ok=True)
                raise ValueError(f"Output non valido ({ext}): {val_err}") from val_err

            # NOTA: la pubblicazione automatica su ArcGIS (chiamata a
            # upload_imdf_layers dal modulo `app.core.imdf`) qui era stata
            # cablata in modo provvisorio leggendo i campi `imdf_*` dal nodo
            # result. E' stata rimossa: la pubblicazione su ArcGIS sara'
            # gestita da un nodo dedicato (publisher) in una fase futura
            # Il modulo `app.core.imdf` resta disponibile per quel reuse

            # ── ESTRAZIONE METADATI IMDF ─────────────────────────────────
            # Se nel canvas c'e' almeno un nodo api_enrich con
            # output_kind='imdf', il file zip prodotto e' un IMDF: estraiamo
            # manifest.json + validation.json e li includiamo inline nel task
            # result come `result['imdf']`. Il frontend li mostra nei modali
            # del nodo Result senza dover rifetchare il file
            # Sono piccoli (manifest ~10KB, validation ~6KB), inline e' OK
            imdf_metadata: Optional[Dict[str, Any]] = None
            has_imdf_upstream = any(
                (n.get("output_kind") == "imdf" or (n.get("data") or {}).get("output_kind") == "imdf")
                for n in nodes
                if n.get("type") == "api_enrich"
            )
            if has_imdf_upstream and ext == ".zip":
                try:
                    from app.core.imdf.loader import extract_imdf_zip
                    from app.core.imdf.persist import persist_imdf_bundle_to_disk
                    bundle = extract_imdf_zip(str(src_path))

                    # ── Persisti il bundle su disco ────────────────────
                    # NON serializziamo piu' geojson/manifest/validation/renderers
                    # nel task result: restano tutti sul filesystem in
                    # IMDF_BUNDLES_DIR/{task_id}/, il frontend li carica
                    # on-demand via GET /api/imdf/bundles/{task_id}
                    # `persist_imdf_bundle_to_disk` garantisce in try/finally
                    # il cleanup di `bundle.tmp_dir` in OGNI caso (anche
                    # su errore di move), prevenendo orfani `/tmp/imdf_XXXX/`
                    bundle_persisted_path = None
                    try:
                        _res = persist_imdf_bundle_to_disk(
                            bundle, task_id=task_id, project_id=project_id,
                        )
                        bundle_persisted_path = _res["path"]
                    except Exception as _persist_e:
                        logger.warning(
                            f"[Task {task_id}] Persistenza bundle fallita: {_persist_e}"
                        )
                        # cleanup già eseguito dal finally interno di persist

                    # ── Push S3 (R2 multi-host) ──────────────────────────
                    # D3 commit 4 (R2): orchestrazione esplicita persist ->
                    # storage. Pre-commit la chiamata viveva inline dentro
                    # persist (inversione di dep loader/persist <- storage
                    # Post-commit il worker e' l'orchestratore. Best-effort
                    # identico al pre-commit: failure logga warning ma non
                    # blocca il task (il bundle e' comunque servibile dal
                    # nodo corrente via local cache fino a TTL
                    if bundle_persisted_path:
                        try:
                            from app.core.imdf import storage as _imdf_storage
                            _imdf_storage.save_bundle(
                                task_id, Path(_res["path"]), meta=_res["meta"],
                            )
                        except Exception as _save_e:
                            logger.warning(
                                f"[{task_id}] imdf storage upload fallito (non-fatal, "
                                f"bundle disponibile solo sul nodo corrente): {_save_e}"
                            )

                    # ── Task result SLIM ──────────────────────────────
                    # Solo informazioni minime che servono al frontend
                    # per sapere "esiste un bundle IMDF" + link per fetch
                    # lazy. I dettagli vengono da GET /api/imdf/bundles/{id}
                    imdf_metadata = {
                        "site_id":        bundle.site_id,
                        "has_gdb":        bundle.gdb_path is not None,
                        "has_ifc":        bundle.ifc_path is not None,
                        "has_renderers":  bundle.renderers_path is not None,
                        "bundle_task_id": task_id if bundle_persisted_path else None,
                    }
                    logger.info(
                        f"[Task {task_id}] IMDF metadata estratti: "
                        f"site={imdf_metadata['site_id']} "
                        f"has_gdb={imdf_metadata['has_gdb']} "
                        f"has_ifc={imdf_metadata['has_ifc']} "
                        f"has_renderers={imdf_metadata['has_renderers']}"
                    )
                except Exception as _imdf_e:
                    logger.warning(f"[Task {task_id}] Estrazione metadati IMDF fallita: {_imdf_e}")
                    imdf_metadata = {"error": str(_imdf_e)}

            # `result_id` viene generato solo nel ramo arricchisci standard
            # save su DB). Per gli eseguibili pubblicati il file vive su
            # disco e si scarica via /api/eseguibili/.../download, quindi
            # non c'e' result_id da propagare
            result_id: Optional[str] = None

            if is_eseguibile:
                # Upload su object storage (S3 via cloud_backends). Niente FS
                # locale persistente: backend+worker multi-host safe. Il
                # frontend scarica via GET /api/eseguibili/{id}/download/{task_id}
                # che streamma da S3 e cancella l'oggetto dopo l'invio
                from app.core.eseguibili import storage as _eseg_storage
                _output_filename = src_path.name
                _eseg_storage.save(task_id, src_path)
                # Cleanup directory temporanea dell'executable (plenora_exec_*
                try:
                    tmp_parent = src_path.parent
                    if tmp_parent.name.startswith("plenora_exec_") and tmp_parent.exists():
                        _shutil.rmtree(tmp_parent, ignore_errors=True)
                except Exception as e:
                    logger.debug(f"cleanup temp dir fallito: {type(e).__name__}: {e}")
                # src_path non punta piu' a un file locale; conserviamo il nome
                # per il payload del task result (usato come metadata
                src_path = Path(_output_filename)
                logger.info(f"[Task {task_id}] Output eseguibile su storage: eseguibili-outputs/{task_id}/{_output_filename}")
            else:
                # Flusso arricchisci standard: salva nel DB
                from app.utils.result_store import save_result_blob
                result_id = save_result_blob(
                    project_id=project_id,
                    path=src_path,
                    fmt=src_path.suffix.lstrip(".") or "bin",
                    rows_count=rows_count,
                )
                logger.info(f"[Task {task_id}] File result persistito: {result_id}")

            result = {
                "success": True,
                "task_id": task_id,
                "project_id": project_id,
                # `result_id` propagato per il download via
                # `/api/tasks/{task_id}/download` che usa
                # `task_info.result["result_id"]` (vedi
                # orchestrator/router.py:580). Senza, 410 Gone
                "result_id": result_id,
                "output_type": "file",
                "output_file": src_path.name,
                "output_filename": src_path.name,

                "rows_processed": rows_count,
                "columns": [],
                "output_format": src_path.suffix.lstrip("."),
                "elapsed_time": round(elapsed_time, 2),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "execution_time_ms": final_result.get("execution_time_ms", 0),
                "enrichment_stats": None,
                "error_report": None,
                "message": f"Processamento completato: {src_path.name}",
            }
            if imdf_metadata is not None:
                result["imdf"] = imdf_metadata
            if is_eseguibile:
                # URL relativo; il frontend lo compone con il base host
                result["download_url"] = f"/api/eseguibili/{project_id}/download/{task_id}"
            
            logger.info(f"[Task {task_id}] Executable completato in {elapsed_time:.2f}s - {src_path.name}")

            # ── CARICA anche nel branch file-result (saltato per eseguibili pubblicati) ──
            if not is_eseguibile:
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
                        f"[GESTITO] [Task {task_id}] Errore avvio task CARICA dopo rest_file: {_ce}",
                        project_id=project_id, exc_info=True
                    )

            _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
            return result
        
        # ── DATAFRAME RESULT (flusso standard) ──
        final_df = final_result
        
        if final_df is None:
            raise ValueError("Nessun risultato finale")
        # N14: df vuoto (0 righe) è un risultato valido — non crashare
        # salva un file con solo l'header
        if hasattr(final_df, "empty") and final_df.empty:
            logger.info(f"[Task {task_id}] DataFrame vuoto (0 righe) — salvo output con solo header")
        
        # Salva DataFrame come bytes/object su destinazione finale
        # Niente DATA_DIR/temp staging (save_pipeline_result e la variante
        # S3 per eseguibili gestiscono il dispatch internamente
        final_filename = f"{output_filename}.{final_format}"
        elapsed_time = time.time() - start_time
        result_id: Optional[str] = None

        if is_eseguibile:
            # Eseguibile con output DataFrame → S3 via storage module
            # save_bytes fa put_object diretto (zero disk) per formati
            # in-memory-safe. Per fmt fs-only (gpkg/shp) il driver GDAL/OGR
            # richiede un path: usiamo TemporaryDirectory che garantisce
            # rmtree al __exit__ (no orphan su SIGKILL mid-run entro il
            # cleanup OS-level del /tmp
            from app.core.eseguibili import storage as _eseg_storage
            from app.utils.dataframe import save_dataframe, save_dataframe_to_bytes
            fmt_lower = (final_format or "").lower()
            if fmt_lower in ("gpkg", "shp"):
                with _tf.TemporaryDirectory(
                    prefix="urby_eseg_gpkg_shp_",
                    ignore_cleanup_errors=True,
                ) as tmp_dir:
                    tmp_path = Path(tmp_dir) / final_filename
                    saved_local = save_dataframe(
                        df=final_df, output_path=tmp_path, format=final_format,
                    )
                    # Bytes → S3 put_object direct. La dir viene pulita
                    # al __exit__ anche se save_bytes solleva
                    data_bytes = saved_local.read_bytes()
                    _eseg_storage.save_bytes(
                        task_id, saved_local.name, data_bytes,
                    )
            else:
                data_bytes = save_dataframe_to_bytes(final_df, fmt_lower)
                _eseg_storage.save_bytes(task_id, final_filename, data_bytes)
            saved_path = Path(final_filename)  # per metadata payload
            logger.info(f"[Task {task_id}] Output eseguibile (df) su storage: eseguibili-outputs/{task_id}/{final_filename}")
        else:
            # Arricchisci standard: single entry-point save_pipeline_result
            from app.utils.result_store import save_pipeline_result
            saved = save_pipeline_result(
                project_id=project_id,
                df=final_df,
                output_format=final_format,
                filename_stem=output_filename,
                rows_count=len(final_df),
            )
            result_id = saved["result_id"]
            saved_path = Path(saved["output_filename"])
            logger.info(f"[Task {task_id}] Risultato persistito: {result_id}")

            # B-BE-39: salva columns_metadata nel progetto per uso come project_source
            # Helper condiviso (retry transient + raise non-transient): se fallisce
            # in modo permanente, ColumnsMetadataSaveError propaga al try/except
            # esterno di Celery che marca il task FAILED — pre-fix il warning
            # silenzioso lasciava il task SUCCESS con metadata stale
            from app.core.projects.service import ProjectService as _ProjSvc
            from app.shared.columns_metadata import save_columns_metadata_with_retry
            _col_meta = [{"name": col, "type": str(final_df[col].dtype)} for col in final_df.columns]
            save_columns_metadata_with_retry(
                project_service=_ProjSvc(),
                project_id=project_id,
                columns_metadata=_col_meta,
                logger=logger,
                op=f"enrichment_task[{task_id}]:save_columns_metadata",
            )
            logger.info(f"[Task {task_id}] columns_metadata salvato: {len(_col_meta)} colonne")

        update_task_progress(self, 100, "Completato!", task_id=task_id)

        result = {
            "success": True,
            "task_id": task_id,
            "project_id": project_id,
            # `result_id` propagato per il download via
            # `/api/tasks/{task_id}/download` (orchestrator/router.py:580
            # None nel ramo eseguibile: il frontend in quel caso usa
            # `download_url` → `/api/eseguibili/.../download/{task_id}`
            "result_id": result_id,
            "rows_processed": len(final_df),
            "columns": list(final_df.columns),
            "output_file": saved_path.name,
            "output_format": final_format,
            "elapsed_time": round(elapsed_time, 2),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            # Statistiche enrichment
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
        }
        if is_eseguibile:
            result["download_url"] = f"/api/eseguibili/{project_id}/download/{task_id}"

        logger.info(f"[Task {task_id}] Completato in {elapsed_time:.2f}s - {len(final_df)} righe", project_id=_log_pid)

        # Snapshot result node ARRICCHISCI
        # passa per `record_final_snapshot`
        # con module="arricchisci". La policy globale
        # `INTERMEDIATE_PERSISTENCE_MODE` decide il dimensionamento
        # default `preview_only` -> 500 righe + hash + meta
        # Pre-fix: thread daemon che materializzava `final_df.to_dict(full)`
        # nel thread principale prima del dispatch -> double memory peak
        # sui DataFrame grandi
        # Post-fix: chiamata sync. Hash via pandas_hash_v1 senza
        # materializzare. Lo skip-path o il preview-cap rendono il
        # costo trascurabile rispetto al df.to_dict(full) precedente
        if _run_id and result_node and not is_eseguibile:
            try:
                from app.core.intermediate.snapshot import record_final_snapshot
                record_final_snapshot(
                    run_id=_run_id,
                    module="arricchisci",
                    node_id=result_node["id"],
                    node_type="result",
                    df=final_df,
                    exec_time=0.0,
                    status="ok",
                    mode_override=intermediate_mode_override,
                )
            except Exception as _sne:
                logger.warning(f"[Task {task_id}] Intermediate write result node fallita: {_sne}")

        if _run_id and not is_eseguibile:
            _exec_time = time.time() - _run_start
            _rid_c = _run_id
            _retention_c = _retention_n
            def _async_close(_r: str = _rid_c, _t: float = _exec_time,
                             _rn: int | None = _retention_c) -> None:
                try:
                    from app.core.intermediate.writer import close_run as _cr
                    _cr(run_id=_r, input_hash="", output_hash="",
                        total_exec_time=_t, success=True, retention_n=_rn)
                except Exception as e:
                    logger.debug(f"cleanup async close_run fallito: {type(e).__name__}: {e}")
            threading.Thread(target=_async_close, daemon=True).start()

        if not is_eseguibile:
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
        # perché execute_enrichment_task non passa per l'orchestrator
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
        # Catch dei _EarlyTaskReturn sollevati dalla closure _run_single_node
        # anche da thread worker paralleli). Questi portano un payload gia'
        # pronto per il return del task: niente logging CRASH, niente close_run
        # in failure mode, niente debug_payload — e' un fallimento "gestito"
        # Nota: _EarlyTaskReturn e' definita come classe locale dentro il try
        # quindi qui la riconosciamo via name matching sul __class__
        if type(e).__name__ == "_EarlyTaskReturn" and hasattr(e, "payload"):
            if _run_id and not is_eseguibile:
                try:
                    from app.core.intermediate.writer import close_run as _close_run
                    _close_run(run_id=_run_id, input_hash="", output_hash="",
                               total_exec_time=time.time() - _run_start, success=False,
                               retention_n=_retention_n)
                except Exception as e_inner:
                    logger.debug(f"close_run intermediate fallito (early return): {type(e_inner).__name__}: {e_inner}")
            if not is_eseguibile:
                _elapsed_ms_er = int((time.time() - _run_start) * 1000)
                close_run(
                    run_id=_run_id or task_id,
                    status="failure",
                    duration_ms=_elapsed_ms_er,
                    error_module="arricchisci",
                    error_message=e.payload.get("error", "task early return"),
                )
            _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)
            return e.payload
        if _run_id and not is_eseguibile:
            try:
                from app.core.intermediate.writer import close_run as _close_run
                _close_run(run_id=_run_id, input_hash="", output_hash="",
                           total_exec_time=time.time() - _run_start, success=False,
                           retention_n=_retention_n)
            except Exception as e_inner:
                logger.debug(f"close_connection intermediate fallito (outer except): {type(e_inner).__name__}: {e_inner}")
        error_message = str(e)
        _elapsed_ms_f = int((time.time() - _run_start) * 1000)
        logger.error(f"[CRASH] [Task {task_id}] Errore: {error_message}", project_id=_log_pid)

        if not is_eseguibile:
            try:
                from app.utils.debug_payload_builder import DebugPayloadBuilder
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
            except Exception as e:
                logger.debug(f"DebugPayloadBuilder fallito (enrichment): {type(e).__name__}: {e}")
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
            update_task_progress(self, 0, f"Errore: {error_message}", is_error=True, task_id=task_id)
        except Exception as e_prog:
            logger.debug(f"progress update fallito: {type(e_prog).__name__}: {e_prog}")

        # ── Cleanup risorse temporanee anche in caso di errore ──
        _cleanup_temp_file_sources(cleanup_file_source_ids, task_id)

        # Ritorna dict con errore (serializzabile
        return {
            "success": False,
            "task_id": task_id,
            "project_id": project_id,
            "error": error_message,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            # Statistiche parziali se disponibili
            "enrichment_stats": enrichment_stats,
            "error_report": error_report_file,
        }


# FUNZIONI HELPER PER IL TASK


def _cleanup_temp_file_sources(file_source_ids: Optional[List[str]], task_id: str) -> None:
    """Elimina i file source temporanei creati per l'esecuzione (es. eseguibili).

    Non solleva mai eccezioni: in caso di errore logga e prosegue.
    """
    if not file_source_ids:
        return
    try:
        from app.core.files.service import file_source_service
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


def update_task_progress(
    task: object,
    progress: int,
    message: str,
    is_error: bool = False,
    *,
    task_id: Optional[str] = None,
) -> None:
    """
    Aggiorna il progresso del task in Redis.

    Usa sempre lo stato 'PROGRESS' (mai 'FAILURE') per evitare che Celery
    interpreti il task come terminato. Il frontend determina lo stato
    reale dal flag is_error e dall'eventuale prefisso "Errore:" nel messaggio.

    `task_id` keyword obbligatorio quando la chiamata avviene da un
    thread spawned (es. ThreadPoolExecutor del loop parallelo). Celery
    `Task.request.id` e' thread-local: nei thread worker spawned dal
    main thread del task, `self.request.id` ricade su `None` e
    `backend.store_result(task_id=None, ...)` solleva
    "task_id must not be empty. Got None instead.". Passare `task_id`
    esplicito (catturato all'inizio del task da `self.request.id`)
    bypassa il fallback thread-local.
    """
    task.update_state(
        task_id=task_id,
        state="PROGRESS",
        meta={
            "progress": max(0, progress),
            "message": message,
            "is_error": is_error,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


# Helper modulo-level rimossi: usavano il singleton globale
# `api_connection_service` con stato per-execution non isolato fra task
# paralleli. Ora il task celery (`execute_enrichment_task`) crea una
# propria istanza locale di `ApiConnectionService` e chiama direttamente
# `_get_execution_order` / `_load_source_project` / `_load_file_source` /
# `_load_static_values` su di essa. Questo garantisce isolamento del
# tenant binding (`_current_group_id`) fra task concorrenti sullo stesso
# worker process Celery (prefork riusa i fork
# `find_latest_output_file` e `load_dataframe_from_file` erano gia'
# semplici alias di `app.utils.dataframe`: i call site usano direttamente
# l'import canonico


def _trigger_carica_if_present(project_id: str, canvas: Dict, output_format: str = "csv") -> Optional[Dict[str, Any]]:
    """Cerca nodi CARICA (db/arcgis/datasource) collegati al nodo Output e lancia i relativi task.

    Replica la logica di OrchestratorService._execute_carica_if_present, ma viene chiamata
    direttamente dal task di enrichment perché /enrich-async non passa per l'orchestrator.

    Returns: dict {"tasks": [{task_id, node_id}, ...], "message": ...} oppure None se
             nessun nodo carica trovato. Il frontend usa questo per fare polling sui task.
    """
    nodes = canvas.get("nodes", []) or []
    edges = canvas.get("edges", []) or []

    # Trova nodo Output (result
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

    from app.core.orchestrator.task_service import (
        CaricaTaskPayload,
        TaskSubmitRequest,
        TaskType,
        task_service,
    )
    from app.core.projects.service import ProjectService
    from workers.tasks.carica_task import execute_carica_task
    try:
        metadata = ProjectService().get_project_metadata(project_id)
        group_id = getattr(metadata, 'group_id', None) if metadata else None
    except Exception as e:
        logger.debug(f"group_id metadata lookup fallito: {type(e).__name__}: {e}")
        group_id = None

    tasks = []
    for carica_node in carica_nodes:
        try:
            # Merge node root + node['data'] (stesso pattern dell'orchestratore
            node_dict = dict(carica_node)
            nested_data = node_dict.get("data") or {}
            carica_node_data = {**node_dict, **nested_data}

            task_info = task_service.submit_task(
                TaskSubmitRequest(
                    celery_task=execute_carica_task,
                    task_type=TaskType.CARICA,
                    project_id=project_id,
                    payload=CaricaTaskPayload(
                        project_id=project_id,
                        canvas=canvas,
                        carica_node_data=carica_node_data,
                        output_format=output_format,
                        triggered_by="manual",
                    ),
                    queued_message="Task CARICA in coda...",
                    log_label="carica",
                )
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
