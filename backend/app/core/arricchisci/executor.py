"""
Entry point per l'esecuzione di progetti ARRICCHISCI.

Architettura
============
Questo modulo è il punto di ingresso per eseguire un progetto di tipo
ARRICCHISCI. Riceve il canvas state dal frontend, estrae la configurazione
API e i mapping, e delega l'esecuzione effettiva a EnrichmentExecutor
(enrichment_service.py) o ApiConnectionService (api_connector.py).

Responsabilità:
- Parsing del canvas state per estrarre configurazione arricchimento
- Routing alla strategia di esecuzione corretta
- Gestione risultati e salvataggio output

Questo file è intenzionalmente snello: la logica complessa è nei moduli
specializzati (enrichment_service per l'esecuzione avanzata, api_connector
per le connessioni API).

Chiamato da: core/orchestrator/service.py quando il progetto è di tipo ARRICCHISCI
"""

import time as _time
from typing import Dict, Any, Optional

from app.models import CanvasState
from app.config import settings
from app.utils.logger import get_logger, append_step
from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.canvas_orchestrator import (
    get_execution_order,
    init_execution_context,
)
from app.shared.columns_metadata import save_columns_metadata_with_retry

logger = get_logger(__name__)


class EnrichExecutor:
    """Gestisce esecuzione per progetti ARRICCHISCI."""
    
    def __init__(self, project_service):
        self.project_service = project_service
        self.results_base_dir = settings.RESULTS_DIR

    def execute(
        self,
        project_id: str,
        canvas: CanvasState,
        force_recalculate: bool = False,
        format: str = 'csv',
        run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Esegue la pipeline di arricchimento tramite API esterne.

        `intermediate_mode_override` (R-25 PR-B2.b): kwarg passing-through
        a `api_connection_service.execute_enrichment`. None = bit-compat.
        """
        logger.info(f"Esecuzione ARRICCHISCI: {project_id}", op="execute", project_id=project_id)
        _step_start = _time.time()

        canvas_dict = canvas.model_dump()

        result = api_connection_service.execute_enrichment(
            project_id=project_id,
            canvas=canvas_dict,
            format=format,
            force_recalculate=force_recalculate,
            run_id=run_id,
            intermediate_mode_override=intermediate_mode_override,
        )

        if result.get("success"):
            # `api_connector.execute_enrichment` ha gia' chiamato
            # `save_result_blob` internamente e ritorna `result_id`
            # come riferimento canonico al blob nel DB results.
            # Propaghiamo `result_id` verso l'alto cosi' il
            # `PipelineArtifact.artifact_kind` risolve come `"blob"`
            # invece di tentare di leggere `output_path` stale (gia'
            # unlink-ato dal save_result_blob inner).
            if result.get("output_type") == "file":
                res = {
                    "success": True,
                    "result_id": result.get("result_id"),
                    "output_type": "file",
                    "output_filename": result.get("output_filename", ""),
                    "output_size_bytes": result.get("output_size_bytes", 0),
                    "rows_count": 0,
                    "columns": [],
                    "execution_time_ms": result.get("execution_time_ms", 0),
                    "message": result.get("message", "Processamento completato"),
                }
            else:
                res = {
                    "success": True,
                    "result_id": result.get("result_id"),
                    "output_filename": result.get("output_filename", ""),
                    "rows_count": result.get("rows_processed", 0),
                    "columns": result.get("columns", []),
                    "message": f"Arricchimento completato: {result.get('rows_processed', 0)} righe processate",
                }

                # Allinea columns_metadata del progetto allo schema reale del DataFrame finale.
                # Fix desincronizzazione UI/DB del nodo result — il file xlsx era già corretto
                # ma columns_metadata restava stale e rompeva il riuso come project_source.
                # Helper condiviso: retry transient + raise non-transient (vedi shared/columns_metadata.py).
                cm = result.get("columns_metadata") or []
                if cm:
                    save_columns_metadata_with_retry(
                        project_service=self.project_service,
                        project_id=project_id,
                        columns_metadata=cm,
                        logger=logger,
                    )
        else:
            res = {
                "success": False,
                "message": result.get("error", "Errore durante l'arricchimento")
            }

        # --- Step tracking ---
        append_step({
            "module":      "arricchisci",
            "status":      "success" if res.get("success") else "failure",
            "rows_out":    res.get("rows_count", 0) or 0,
            "duration_ms": int((_time.time() - _step_start) * 1000),
            "error":       res.get("message") if not res.get("success") else None,
        })
        return res

    def preview(self, project_id: str, canvas: CanvasState, limit: int = 10) -> Dict[str, Any]:
        """
        Preview ARRICCHISCI: replica il loop dell'enrichment_task ma in modo sincrono,
        senza Celery, e tronca l'output a `limit` righe.
        Gestisce FeatureCollection explosion come il task reale.
        """
        logger.info(f"Preview ARRICCHISCI: {project_id} (limit={limit})", op="preview", project_id=project_id)
        try:
            import copy
            import pandas as pd
            from app.core.arricchisci.enrichment_service import EnrichmentExecutor
            from app.core.arricchisci.models import EnrichmentOptions
            from app.utils.dataframe import sanitize_for_json

            canvas_dict = canvas.model_dump()
            nodes = canvas_dict.get("nodes", [])
            edges = canvas_dict.get("edges", [])

            if not nodes:
                return {"success": False, "message": "Nessun nodo nel canvas"}

            # BE-GO-READY-TRANSFORMATION-ENGINE-031: preview HTTP usa
            # gli stessi loader ctx-strict del path canvas/task. Dopo
            # TE-030B i loader non leggono piu' `_current_group_id` dal
            # singleton; senza ctx questo endpoint fail-chiuderebbe su
            # file_source/project_source. `init_execution_context`
            # ritorna un typed ctx senza scrivere il mirror legacy;
            # i tre loader sotto ricevono `ctx=ctx` esplicitamente.
            ctx = init_execution_context(api_connection_service, project_id, limit)

            # Carica tutte le API connections con credenziali decifrate (1 query)
            api_connections = api_connection_service.list_api_connections_decrypted()

            # Ordine topologico
            # BE-GO-READY-TRANSFORMATION-ENGINE-014 / CONNECTION-ENGINE-009:
            # callsite allineato alla pure function modulo-level
            # `canvas_orchestrator.get_execution_order(...)`. Behavior
            # byte-per-byte identico (Kahn topo-sort deterministico).
            execution_order = get_execution_order(nodes, edges)
            node_map = {n["id"]: n for n in nodes}
            node_results = {}

            result_df = None

            for node_id in execution_order:
                node = node_map.get(node_id)
                if not node:
                    continue

                node_type = node.get("type")
                # _nd: legge da root poi da data
                def _nd(n, k, d=None):
                    v = n.get(k)
                    if v is not None:
                        return v
                    return n.get("data", {}).get(k, d) if isinstance(n.get("data"), dict) else d

                node_data = {k: _nd(node, k) for k in list(node.keys()) + list((node.get("data") or {}).keys())}
                node_data.update(node)  # root keys override

                if node_type == "staticValues":
                    node_results[node_id] = api_connection_service._load_static_values(
                        node_data,
                        ctx=ctx,
                    )

                elif node_type == "file_source":
                    node_results[node_id] = api_connection_service._load_file_source(
                        node_data,
                        ctx=ctx,
                    )

                elif node_type == "project_source":
                    node_results[node_id] = api_connection_service._load_source_project(
                        node_data,
                        False,
                        ctx=ctx,
                    )

                elif node_type == "api_enrich":
                    api_id = node_data.get("api_id")
                    api_config = api_connections.get(api_id)
                    if not api_config:
                        continue

                    input_edge = next((e for e in edges if e["target"] == node_id), None)
                    input_df = node_results.get(input_edge["source"]) if input_edge else None

                    # ── execute_if: skip condizionale ──
                    from app.core.arricchisci.parallel import should_execute_node
                    if not should_execute_node(node_data, input_df):
                        logger.info(f"Preview: nodo {node_id} skippato (execute_if)")
                        node_results[node_id] = input_df
                        result_df = input_df
                        continue

                    if api_config.get("api_type", "enricher") == "generator" or input_df is None:
                        result_df = api_connection_service._execute_api_generator(api_config, input_df)
                        if isinstance(result_df, dict):
                            result_df = None
                    else:
                        # Enricher standard — stesso path del task reale
                        raw_output_mapping = node_data.get("output_mapping") or api_config.get("output_mapping", [])
                        seen_cols = set()
                        deduped = [m for m in (raw_output_mapping or [])
                                   if m.get("column") or m.get("path") not in seen_cols
                                   and not seen_cols.add(m.get("column") or m.get("path"))]

                        exec_options = EnrichmentOptions(
                            enable_deduplication=True,
                            continue_on_error=True,
                            generate_error_report=False,
                            skip_null_params=True,
                            rate_limit=api_config.get("options", {}).get("rate_limit", settings.API_DEFAULT_RATE_LIMIT),
                        )
                        api_cfg = {**api_config, "output_mapping": deduped}
                        param_mapping = node_data.get("param_mapping", {})

                        executor = EnrichmentExecutor(
                            api_config=api_cfg,
                            param_mapping=param_mapping,
                            options=exec_options
                        )
                        # Tronca a `limit` righe PRIMA dell'enrichment
                        # (evita di chiamare l'API su tutto il dataset per una preview)
                        preview_input = input_df.head(limit) if len(input_df) > limit else input_df
                        result_df, _ = executor.execute(preview_input)

                    node_results[node_id] = result_df

                elif node_type == "result":
                    # Prendi l'ultimo DataFrame prodotto
                    input_edge = next((e for e in edges if e["target"] == node_id), None)
                    if input_edge:
                        result_df = node_results.get(input_edge["source"])

            if result_df is None or (hasattr(result_df, 'empty') and result_df.empty):
                return {"success": False, "message": "Nessun dato prodotto dalla preview"}

            total_count = len(result_df)
            preview_df = result_df.head(limit)

            # ── Mappa dtype pandas → tipo Plenora ──────────────────────
            _GEO_NAMES = {"geometry", "geom", "shape", "wkt", "wkb", "geo",
                          "the_geom", "geom_4326", "geom_wgs84"}
            _WKT_PFX = ("POINT", "LINESTRING", "POLYGON", "MULTIPOINT",
                        "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION")

            def _col_type(name: str, series) -> str:
                if name.lower().strip() in _GEO_NAMES:
                    return "geometry"
                dtype = str(series.dtype)
                if dtype.startswith("int"):   return "int"
                if dtype.startswith("float"): return "real"
                if dtype == "bool":           return "bool"
                if dtype.startswith("datetime"): return "date"
                if dtype == "object":
                    for val in series.dropna().head(3):
                        if not isinstance(val, str):
                            break
                        v = val.strip()
                        if v.upper().startswith(_WKT_PFX):
                            return "geometry"
                        if v.startswith("{") and '"type"' in v and '"coordinates"' in v:
                            return "geometry"
                return "string"

            columns = [{"name": c, "type": _col_type(c, preview_df[c])} for c in preview_df.columns]
            rows = sanitize_for_json(preview_df.to_dict(orient="records"))

            return {
                "success": True,
                "columns": columns,
                "rows": rows,
                "total_count": total_count,
                "preview_count": len(rows),
                "truncated": total_count > limit
            }

        except Exception as e:
            logger.error(f"[GESTITO] Errore preview ARRICCHISCI: {e}", op="preview", exc_info=True)
            return {"success": False, "message": str(e)}
