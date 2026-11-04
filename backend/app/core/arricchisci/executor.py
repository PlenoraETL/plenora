"""
Entry point per l'esecuzione di progetti ARRICCHISCI.

Keeps API enrichment behavior isolated from orchestration callers.
"""

from app.core.projects.schema_inference import SchemaInferenceService
import time as _time
from typing import Any, Mapping, Optional

from app.core.projects.canvas_models import CanvasState
from app.core.projects.canvas_preview_scope import preview_canvas
from app.utils.logger import get_logger, append_step
from app.core.arricchisci.api_connector import api_connection_service
from app.core.arricchisci.execution_context import ArricchisciExecutionContext
from app.core.arricchisci.canvas_orchestrator import execute_canvas_enrichment
from app.core.arricchisci.types import JsonObject
from app.core.dataframes.tabular import dataframe_preview_records, non_null_head_values
from app.core.output_contract import persist_output_contract, result_columns_from_plan

logger = get_logger(__name__)


# ── Mappa dtype pandas → tipo Plenora (preview) ───────────────────────────
_GEO_NAMES = {"geometry", "geom", "shape", "wkt", "wkb", "geo",
              "the_geom", "geom_4326", "geom_wgs84"}
_WKT_PFX = ("POINT", "LINESTRING", "POLYGON", "MULTIPOINT",
            "MULTILINESTRING", "MULTIPOLYGON", "GEOMETRYCOLLECTION")


def _preview_col_type(name: str, series) -> str:
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


class EnrichExecutor:
    """Gestisce esecuzione per progetti ARRICCHISCI."""
    
    def __init__(self, project_service):
        self.project_service = project_service

    def execute(
        self,
        project_id: str,
        canvas: CanvasState,
        force_recalculate: bool = False,
        format: str = 'csv',
        run_id: str = None,
        intermediate_mode_override: Optional[str] = None,
        execution_context: Optional[Mapping[str, Any]] = None,
    ) -> JsonObject:
        """Esegue la pipeline di arricchimento tramite API esterne.

        `intermediate_mode_override` (R-25 PR-B2.b): kwarg passing-through
        a `api_connection_service.execute_enrichment`. None = bit-compat.
        """
        logger.info(f"Esecuzione ARRICCHISCI: {project_id}", op="execute", project_id=project_id)
        _step_start = _time.time()

        canvas_dict = canvas.model_dump(exclude_unset=True)

        typed_context = None
        if execution_context is not None:
            typed_context = ArricchisciExecutionContext(
                project_id=project_id,
                group_id=str(execution_context.get("group_id") or "") or None,
                preview_limit=None,
                output_task_id=(
                    str(execution_context.get("output_task_id") or "") or None
                ),
            )

        final_dataframe = None

        def finalize_dataframe(
            frame: Any, output_name: str, output_format: str, owner_project_id: str,
        ) -> JsonObject:
            nonlocal final_dataframe
            final_dataframe = frame
            return api_connection_service._save_and_build_dataframe_response(
                frame, output_name, output_format, owner_project_id,
            )

        result = api_connection_service.execute_enrichment(
            project_id=project_id,
            canvas=canvas_dict,
            format=format,
            force_recalculate=force_recalculate,
            run_id=run_id,
            intermediate_mode_override=intermediate_mode_override,
            execution_context=typed_context,
            finalizer=finalize_dataframe,
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
                result_columns = result_columns_from_plan(
                    project_id=project_id, columns=result_names, plan=[], physical_names=physical_names,
                    dataframe=final_dataframe,
                )
                try:

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
                        result_columns = result_columns_from_plan(
                            project_id=project_id, columns=result_names, plan=result_schema,
                            physical_names=physical_names, dataframe=final_dataframe,
                        )
                except Exception as exc:
                    # WARNING con exc_info: livello non bloccante, ma traceback
                    # completo per bug SchemaInferenceService.
                    logger.warning(
                        f"output_contract arricchisci: schema id non disponibili: "
                        f"{type(exc).__name__}: {exc}",
                        exc_info=True,
                    )
                # Il blob di arricchimento e' GIA' stato salvato a monte
                # da `api_connector.execute_enrichment` (irreversibile a
                # questo punto). Se l'aggiornamento del contract fallisce
                # qui, il blob resta persistito ma il progetto continua
                # ad esporre l'output_contract precedente — situazione
                # ambigua ma preferibile a far crashare l'intero `execute`
                # (e perdere l'append_step di success). Log ERROR e segnale
                # esplicito in `res` indicano che il contract e' stale.
                try:
                    persist_output_contract(
                        project_service=self.project_service,
                        project_id=project_id,
                        result_columns=result_columns,
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

    def preview(
        self,
        project_id: str,
        canvas: CanvasState,
        limit: int = 10,
        execution_context: Optional[Mapping[str, Any]] = None,
    ) -> JsonObject:
        """Preview ARRICCHISCI: riusa il loop canonico `execute_canvas_enrichment`
        con `preview_limit`, e un finalizer che costruisce le righe in-memory
        senza persistere il blob.

        Step C unificazione: elimina la reimplementazione sincrona del loop.
        FeatureCollection explosion, parallel groups, rest_file e lo skip
        `execute_if` sono ora ereditati dal path canonico invece di essere
        ricodificati qui.
        """
        logger.info(f"Preview ARRICCHISCI: {project_id} (limit={limit})", op="preview", project_id=project_id)
        node_id = (execution_context or {}).get("preview_node_id")
        if node_id is not None:
            canvas = preview_canvas(canvas, str(node_id), with_result=True)
        canvas_dict = canvas.model_dump()

        typed_context = None
        if execution_context is not None:
            typed_context = ArricchisciExecutionContext(
                project_id=project_id,
                group_id=str(execution_context.get("group_id") or "") or None,
                preview_limit=limit,
                output_task_id=None,
            )

        def _finalize_preview(final_df, _output_name, _output_format, _project_id):
            return self._build_preview_response(final_df, limit)

        result = execute_canvas_enrichment(
            api_connection_service,
            project_id=project_id,
            canvas=canvas_dict,
            preview_limit=limit,
            finalizer=_finalize_preview,
            execution_context=typed_context,
        )

        # Mappa gli early-return del loop canonico (error dict / file-result)
        # al contratto preview {success, message}. Quando il finalizer gira,
        # `result` ha gia' la shape preview (success + rows/columns).
        if not result.get("success"):
            return {
                "success": False,
                "message": result.get("error")
                or result.get("message")
                or "Nessun dato prodotto dalla preview",
            }
        if result.get("output_type") == "file":
            return {
                "success": False,
                "message": "Preview non disponibile per output di tipo file",
            }
        if node_id is not None:
            result["approximate"] = True
        return result

    def _build_preview_response(self, result_df: Any, limit: int) -> JsonObject:
        """Costruisce la response di preview (colonne tipate + righe troncate)
        dal DataFrame finale, senza persistere. Finalizer del loop canonico.
        """
        result_columns = list(getattr(result_df, "columns", []))
        if hasattr(result_df, "empty") and result_df.empty and not result_columns:
            return {"success": False, "message": "Nessun dato prodotto dalla preview"}

        total_count = len(result_df)
        preview_df = result_df.head(limit)
        columns = [
            {"name": c, "type": _preview_col_type(c, preview_df[c])}
            for c in preview_df.columns
        ]
        rows = dataframe_preview_records(result_df, limit)

        return {
            "success": True,
            "columns": columns,
            "rows": rows,
            "total_count": total_count,
            "preview_count": len(rows),
            "truncated": total_count > limit,
        }
