"""Preparation and estimate of each API node, without executing external APIs."""
from app.shared.contracts.payloads import JsonObject, JsonRows

import hashlib
import json
from typing import Any

from app.core.arricchisci.request_preparation import prepare_api_request
from app.core.arricchisci.request_preparation import prepare_generator_preflight
from app.core.arricchisci.enrichment_batch_mode import prepare_batch_rows
from app.core.arricchisci.api_connection_generator_enricher import ApiConnectionGeneratorEnricherMixin
from app.core.arricchisci.enrichment_service import build_enrichment_executor
from app.core.arricchisci.canvas_orchestrator import _merge_node_data
from app.core.arricchisci.parallel import get_execute_mask
from app.core.eseguibili.manifest import compile_manifest
from app.core.orchestrator.runtime_inputs import apply_project_runtime_inputs
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.projects.canvas_models import CanvasState
from app.core.projects.schema_execution_sources import prepare_canvas_data
from app.shared.execution.node_fields import node_field
from app.shared.execution.runtime_identity import RuntimeIdentity
from app.shared.execution.runtime_bound_fields import with_runtime_static_parameters


def prepare_contract(contract: PipelineExecutionContract, inputs: JsonObject, *,
                     identity: RuntimeIdentity) -> tuple[PipelineExecutionContract, Any]:
    manifest = compile_manifest(for_project=True, source_project_id=contract.project_id, project_type=contract.project_type,
                                canvas=contract.canvas, default_connection_id=contract.runtime_context.get("default_connection_id"))
    return apply_project_runtime_inputs(contract, inputs, identity=identity), manifest


def preparation_fingerprint(contract: PipelineExecutionContract, connections: JsonObject, *, revision: Any = None,
                            force_recalculate: bool = False) -> str:
    content = {"canvas": contract.canvas, "context": contract.runtime_context, "group_id": contract.group_id,
               "connections": connections, "revision": revision, "force_recalculate": force_recalculate}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()


def estimate_canvas(contract: PipelineExecutionContract, connections: JsonObject) -> JsonRows:
    canvas = CanvasState(**contract.canvas)
    preparation = prepare_canvas_data(canvas, contract.group_id)
    estimates = []
    for node in canvas.nodes:
        if node.type != "api_enrich":
            continue
        api_id = node_field(node, "api_id")
        api = connections.get(api_id)
        if not api:
            raise ValueError(f"API '{api_id}' non trovata")
        data = _merge_node_data(node.model_dump())
        frame, sampled, reason = preparation.input(node.id)
        generator = api.get("api_type") == "generator" or not preparation.input_ids(node.id)
        estimate = estimate_api_input(data, api, frame, sampled=sampled, reason=reason, generator=generator)
        estimate["node_id"] = node.id
        estimates.append(estimate)
    return estimates


def estimate_api_input(data: JsonObject, api: JsonObject, frame: Any, *, sampled: bool = False,
                       reason: str | None = None, generator: bool = False) -> JsonObject:
    """Common preparation for canvas and standalone file estimates."""
    # Stessi parametri dell'esecuzione: input runtime del nodo sopra la connessione.
    api = with_runtime_static_parameters(api, data)
    estimate = {"input_rows": len(frame) if frame is not None else None,
                "sampled": sampled, "sampled_rows": len(frame) if frame is not None else 0,
                "status": "unknown", "api_calls": None, "reason": reason, "valid": True,
                "external_dependencies": "not_guaranteed"}
    if api.get("connection_type") == "rest_file":
        from app.core.arricchisci.api_connector import api_connection_service
        _, error = api_connection_service._parse_rest_file_config(api)
        if error:
            raise ValueError(error["error"])
        estimate.update(status="estimated", api_calls=1,
                        reason="Un invio file previsto; disponibilità file, risposta e retry non garantibili")
    elif generator or api.get("api_type") == "generator":
        prepare_generator_preflight(api)
        ApiConnectionGeneratorEnricherMixin._build_generator_base_params(None, api, frame)
        paginated = bool((api.get("pagination") or {}).get("type"))
        estimate.update(status="unknown" if paginated else "estimated", api_calls=None if paginated else 1,
                        initial_api_calls=1, reason="Richiesta iniziale; pagine e retry dipendono dalla risposta esterna")
    elif frame is not None:
        prepare_api_request(api)
        executor = build_enrichment_executor(data, api)
        validation = executor.validate_input(frame)
        estimate["validation"] = validation.model_dump() if hasattr(validation, "model_dump") else vars(validation)
        estimate["valid"] = validation.valid
        mask = get_execute_mask(data, frame)
        effective = frame if mask is None else frame[mask]
        keys = set()
        count = 0
        for _, row in effective.iterrows():
            params = executor._build_params(row)
            if executor.options.skip_null_params and executor._has_null_params(params):
                continue
            key = executor._make_cache_key({k: v for k, v in params.items() if k != executor.auth.get("key_name", "apiKey")})
            if not executor.options.enable_deduplication or key not in keys:
                count += 1
            keys.add(key)
        if executor.batch_config.enabled:
            batch_rows = prepare_batch_rows(executor, effective)
            size = executor.batch_config.max_size
            count = (len(batch_rows) + size - 1) // size
        estimate.update(status="estimated", api_calls=count, effective_rows=len(effective),
                        reason="Stima richieste senza retry, cache esterna o pagine aggiuntive" + ("; ingressi campionati" if sampled else ""),
                        input_rows_status="estimated" if sampled else "exact")
    else:
        prepare_api_request(api)
    return estimate
