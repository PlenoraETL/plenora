"""Risoluzione condivisa degli input progetto prima dell'invio al worker."""

from collections.abc import Mapping

from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.runtime import resolve_runtime_inputs
from app.core.eseguibili.service import resolve_runtime_connection_id
from app.core.orchestrator.execution_contract import PipelineExecutionContract


def apply_project_runtime_inputs(
    execution_contract: PipelineExecutionContract,
    raw_inputs: Mapping[str, object],
) -> PipelineExecutionContract:
    """Congela canvas e connessione risolti prima dell'invio al worker."""

    nodes = execution_contract.canvas.get("nodes") or []
    has_explicit_inputs = any(
        isinstance(node, Mapping) and node.get("type") == "project_input"
        for node in nodes
    )
    if not has_explicit_inputs:
        if raw_inputs:
            raise ValueError("Il progetto non dichiara input runtime")
        return execution_contract

    manifest = compile_manifest(
        source_project_id=execution_contract.project_id,
        project_type=execution_contract.project_type,
        canvas=execution_contract.canvas,
        default_connection_id=execution_contract.runtime_context.get(
            "default_connection_id"
        ),
    )
    # Nel progetto i file appartengono al canvas: nessuna sostituzione runtime.
    file_keys = {item.key for item in manifest.inputs if item.kind == "file"}
    if file_keys.intersection(raw_inputs):
        raise ValueError(
            "Gli input file runtime sono disponibili solo negli eseguibili"
        )
    manifest = manifest.model_copy(update={
        "inputs": [item for item in manifest.inputs if item.key not in file_keys],
        "bindings": [item for item in manifest.bindings if item.input_key not in file_keys],
    })
    resolved_canvas, _, input_summary = resolve_runtime_inputs(
        eseguibile_id=execution_contract.project_id,
        canvas=execution_contract.canvas,
        manifest=manifest,
        values=raw_inputs,
        group_id=execution_contract.group_id,
    )
    runtime_connection_id = resolve_runtime_connection_id(
        manifest,
        raw_inputs=raw_inputs,
        group_id=execution_contract.group_id,
    )
    context = dict(execution_contract.runtime_context)
    if runtime_connection_id:
        context["default_connection_id"] = runtime_connection_id
    context["runtime_input_summary"] = input_summary
    return execution_contract.model_copy(
        update={"canvas": resolved_canvas, "runtime_context": context}
    )
