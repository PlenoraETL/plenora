"""Risoluzione condivisa degli input progetto prima dell'invio al worker."""

import copy
from collections.abc import Mapping

from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableManifest
from app.core.eseguibili.runtime import resolve_runtime_inputs
from app.core.eseguibili.service import resolve_runtime_connection_id
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.shared.execution.runtime_bound_fields import RUNTIME_BOUND_REGISTRY, runtime_bound_context
from app.shared.execution.runtime_identity import RuntimeIdentity, identity_input_nodes
from app.shared.execution.runtime_input_kinds import node_value, project_input_role


def _project_manifest(execution_contract: PipelineExecutionContract) -> ExecutableManifest:
    return compile_manifest(
        source_project_id=execution_contract.project_id,
        project_type=execution_contract.project_type,
        canvas=execution_contract.canvas,
        default_connection_id=execution_contract.runtime_context.get(
            "default_connection_id"
        ),
    )


def apply_project_runtime_inputs(
    execution_contract: PipelineExecutionContract,
    raw_inputs: Mapping[str, object],
    *,
    identity: RuntimeIdentity,
) -> PipelineExecutionContract:
    """Congela canvas e connessione risolti prima dell'invio al worker.

    ``identity`` e' obbligatoria: «Esegui» e la stima del progetto passano
    ``RuntimeIdentity.project_run()`` (un input «Utente che esegue» blocca),
    anteprima e Valida ``RuntimeIdentity.preview_of(canvas)``.
    """

    nodes = execution_contract.canvas.get("nodes") or []
    has_explicit_inputs = any(
        isinstance(node, Mapping) and node.get("type") == "project_input"
        for node in nodes
    )
    if not has_explicit_inputs:
        if raw_inputs:
            raise ValueError("Il progetto non dichiara input runtime")
        return execution_contract

    manifest = _project_manifest(execution_contract)
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
        identity=identity,
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
    # Registro dei campi scritti dalla risoluzione (regola del lettore).
    context.pop(RUNTIME_BOUND_REGISTRY, None)
    context.update(runtime_bound_context(resolved_canvas))
    return execution_contract.model_copy(
        update={"canvas": resolved_canvas, "runtime_context": context}
    )


def apply_identity_inputs(
    execution_contract: PipelineExecutionContract,
    identity: RuntimeIdentity,
) -> PipelineExecutionContract:
    """Anteprime dell'editor: valorizza SOLO gli input «Utente che esegue»
    (con lo username di prova) e lascia gli altri input come oggi.

    REGOLA (correzione dopo la conferma, punto 4): la presenza di un input
    «Utente che esegue» cambia SOLO quell'input. Il manifest si compila sul
    canvas senza gli ALTRI nodi «Input di progetto» (un altro input mal
    configurato non blocca l'anteprima) e quei nodi, con i loro archi,
    tornano nel canvas risolto come erano: anteprima e valori distinti li
    trattano esattamente come in un canvas senza il nodo utente.

    Canvas senza input di tipo server: invariato.
    """

    canvas = execution_contract.canvas
    if not identity_input_nodes(canvas):
        return execution_contract
    identity_ids = {str(node_value(node, "id") or "") for node in identity_input_nodes(canvas)}
    nodes = list(canvas.get("nodes") or [])
    edges = list(canvas.get("edges") or [])
    other_input_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, Mapping) and node.get("type") == "project_input"
        and str(node.get("id")) not in identity_ids
    }

    # Un input «Utente che esegue» non collegato a nessun campo non cambia
    # nulla: in anteprima si toglie come farebbe la risoluzione (un input
    # testo non collegato, qui, non blocca: nemmeno questo).
    unlinked_ids = {
        str(node.get("id"))
        for node in nodes
        if isinstance(node, Mapping) and str(node.get("id")) in identity_ids
        and project_input_role(node) == "parameter"
        and not any(isinstance(b, Mapping) and b.get("target_field")
                    for b in (node_value(node, "bindings") or []))
    }
    dropped = other_input_ids | unlinked_ids
    identity_only = {
        **canvas,
        "nodes": [node for node in nodes if not (isinstance(node, Mapping) and str(node.get("id")) in dropped)],
        "edges": [edge for edge in edges if not (isinstance(edge, Mapping) and (
            str(edge.get("source")) in dropped or str(edge.get("target")) in dropped))],
    }
    contract = execution_contract.model_copy(update={"canvas": identity_only})
    manifest = _project_manifest(contract)
    resolved_canvas, _, _summary = resolve_runtime_inputs(
        eseguibile_id=execution_contract.project_id,
        canvas=identity_only,
        manifest=manifest,
        values={},
        group_id=execution_contract.group_id,
        identity=identity,
    )
    # Gli altri input tornano come erano (stesso ordine del canvas).
    resolved_by_id = {
        str(node.get("id")): node for node in resolved_canvas.get("nodes") or [] if isinstance(node, Mapping)
    }
    resolved_canvas["nodes"] = [
        copy.deepcopy(node) if str(node.get("id")) in other_input_ids else resolved_by_id[str(node.get("id"))]
        for node in nodes
        if isinstance(node, Mapping)
        and (str(node.get("id")) in other_input_ids or str(node.get("id")) in resolved_by_id)
    ]
    removed_ids = {
        str(node.get("id")) for node in nodes if isinstance(node, Mapping)
    } - other_input_ids - set(resolved_by_id)
    resolved_canvas["edges"] = [
        copy.deepcopy(edge)
        for edge in edges
        if not (isinstance(edge, Mapping) and (
            str(edge.get("source")) in removed_ids or str(edge.get("target")) in removed_ids
        ))
    ]
    context = dict(execution_contract.runtime_context)
    context.pop(RUNTIME_BOUND_REGISTRY, None)
    context.update(runtime_bound_context(resolved_canvas))
    return execution_contract.model_copy(update={"canvas": resolved_canvas, "runtime_context": context})
