"""
Entry point per l'esecuzione di progetti ARRICCHISCI.

Keeps API enrichment behavior isolated from orchestration callers.
"""

import time as _time
from typing import Optional

from app.core.projects.canvas_models import CanvasState
from app.config import settings
from app.utils.logger import get_logger, append_step
from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.canvas_orchestrator import (
    get_execution_order,
    init_execution_context,
)
from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import EnrichmentOptions
from app.core.arricchisci.types import JsonObject
from app.core.arricchisci.parallel import should_execute_node
from app.core.dataframes.tabular import dataframe_preview_records, non_null_head_values
from app.core.output_contract import build_output_contract

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
    ) -> JsonObject:
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
            # come riferimento canonico al blob nel DB results
            # Propaghiamo `result_id` verso l'alto cosi' il
            # `PipelineArtifact.artifact_kind` risolve come `"blob"`
            # invece di tentare di leggere `output_path` stale (gia'
            # unlink-ato dal save_result_blob inner
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

                result_names = list(result.get("columns") or [])
                output_columns = result.get("output_columns")
                physical_names = (
                    output_columns
                    if isinstance(output_columns, list)
                    and len(output_columns) == len(result_names)
                    else result_names
                )
                result_columns = [
                    {
                        "source_name": str(col),
                        "name": str(physical_name),
                        "type": "str",
                    }
                    for col, physical_name in zip(result_names, physical_names)
                ]
                try:
                    from app.core.projects.schema_inference import SchemaInferenceService

                    schema_service = SchemaInferenceService(self.project_service)
                    nodes_by_id = {n.id: n for n in canvas.nodes}
                    edges_by_target = schema_service._group_edges_by_target(canvas.edges)
                    result_node = next((n for n in canvas.nodes if n.type == "result"), None)
                    if result_node is not None:
                        result_schema = schema_service._infer_node_schema(
                            node=result_node,
                            nodes_by_id=nodes_by_id,
                            edges_by_target=edges_by_target,
                            project_id=project_id,
                        )
                        by_name = {col.name: col for col in result_schema}
                        result_columns = [
                            {
                                "id": getattr(by_name.get(col), "id", None),
                                "source_name": getattr(by_name.get(col), "source_name", None) or col,
                                "name": physical_name,
                                "type": "str",
                            }
                            for col, physical_name in zip(result_names, physical_names)
                        ]
                except Exception as exc:
                    logger.warning(
                        f"output_contract arricchisci: schema id non disponibili: {exc}"
                    )
                if result_columns:
                    # Il blob di arricchimento e' GIA' stato salvato a monte
                    # da `api_connector.execute_enrichment` (irreversibile a
                    # questo punto). Se l'aggiornamento del contract fallisce
                    # qui, il blob resta persistito ma il progetto continua
                    # ad esporre l'output_contract precedente — situazione
                    # ambigua ma preferibile a far crashare l'intero `execute`
                    # (e perdere l'append_step di success). Logghiamo come
                    # ERROR (non WARNING: e' un drift di schema osservabile
                    # da downstream) e mettiamo un segnale esplicito nel
                    # `res` cosi' caller/UI sanno che il contract e' stale.
                    # Pre-fix: nessun try/except → eccezione propagava al
                    # task Celery, append_step di success non veniva mai
                    # emesso, e il blob restava orfano dal contract.
                    try:
                        output_contract = build_output_contract(
                            project_id=project_id,
                            result_columns=result_columns,
                            previous_contract=self.project_service.get_project_output_contract(project_id),
                        )
                        self.project_service.update_project_output_contract(
                            project_id,
                            output_contract.model_dump(),
                        )
                    except Exception as contract_err:
                        logger.error(
                            f"[CRASH] update_project_output_contract fallito "
                            f"post-arricchimento: blob persistito, contract "
                            f"resta stale. Caller deve gestire drift: "
                            f"{contract_err}",
                            op="execute", project_id=project_id, exc_info=True,
                        )
                        res["contract_update_failed"] = True
                        res["contract_update_error"] = str(contract_err)
        else:
            res = {
                "success": False,
                "message": result.get("error", "Errore durante l'arricchimento")
            }

        append_step({
            "module":      "arricchisci",
            "status":      "success" if res.get("success") else "failure",
            "rows_out":    res.get("rows_count", 0) or 0,
            "duration_ms": int((_time.time() - _step_start) * 1000),
            "error":       res.get("message") if not res.get("success") else None,
        })
        return res

    def preview(self, project_id: str, canvas: CanvasState, limit: int = 10) -> JsonObject:
        """
        Preview ARRICCHISCI: replica il loop dell'enrichment_task ma in modo sincrono,
        senza Celery, e tronca l'output a `limit` righe.
        Gestisce FeatureCollection explosion come il task reale.
        """
        logger.info(f"Preview ARRICCHISCI: {project_id} (limit={limit})", op="preview", project_id=project_id)
        try:
            canvas_dict = canvas.model_dump()
            nodes = canvas_dict.get("nodes", [])
            edges = canvas_dict.get("edges", [])

            if not nodes:
                return {"success": False, "message": "Nessun nodo nel canvas"}

            ctx = init_execution_context(api_connection_service, project_id, limit)

            # Carica tutte le API connections con credenziali decifrate (1 query
            api_connections = api_connection_service.list_api_connections_decrypted()

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
                        # Enricher standard — stesso path del task reale.
                        # Dedup esplicito via loop (era una list-comprehension
                        # con bug di precedenza: `m.get("column") or
                        # m.get("path") not in seen_cols` valutava come
                        # `column OR (path not in seen)`, quindi quando
                        # `column` era truthy la dedup non scattava mai
                        # e duplicati passavano all'EnrichmentExecutor che
                        # sovrascriveva celle con valori later-in-list).
                        raw_output_mapping = (
                            node_data.get("output_mapping")
                            or api_config.get("output_mapping", [])
                            or []
                        )
                        seen_cols: set[str] = set()
                        deduped: list = []
                        for m in raw_output_mapping:
                            key = m.get("column") or m.get("path")
                            if key in seen_cols:
                                continue
                            seen_cols.add(key)
                            deduped.append(m)

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
                        # evita di chiamare l'API su tutto il dataset per una preview
                        preview_input = input_df.head(limit) if len(input_df) > limit else input_df
                        result_df, _ = executor.execute(preview_input)

                    node_results[node_id] = result_df

                elif node_type == "result":
                    # Prendi l'ultimo DataFrame prodotto
                    input_edge = next((e for e in edges if e["target"] == node_id), None)
                    if input_edge:
                        result_df = node_results.get(input_edge["source"])

            if result_df is None:
                return {"success": False, "message": "Nessun dato prodotto dalla preview"}
            result_columns = list(getattr(result_df, "columns", []))
            if hasattr(result_df, 'empty') and result_df.empty and not result_columns:
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
                if dtype.startswith("int"):
                    return "int"
                if dtype.startswith("float"):
                    return "real"
                if dtype == "bool":
                    return "bool"
                if dtype.startswith("datetime"):
                    return "date"
                if dtype == "object":
                    for val in non_null_head_values(series, 3):
                        if not isinstance(val, str):
                            break
                        v = val.strip()
                        if v.upper().startswith(_WKT_PFX):
                            return "geometry"
                        if v.startswith("{") and '"type"' in v and '"coordinates"' in v:
                            return "geometry"
                return "string"

            columns = [{"name": c, "type": _col_type(c, preview_df[c])} for c in preview_df.columns]
            rows = dataframe_preview_records(result_df, limit)

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
