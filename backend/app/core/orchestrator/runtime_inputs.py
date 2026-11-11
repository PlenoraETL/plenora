"""Risoluzione condivisa degli input progetto prima dell'invio al worker."""

import copy
from collections.abc import Mapping
from typing import Optional

from app.config import settings
from app.core.eseguibili import output_store as _output_store
from app.core.eseguibili.manifest import compile_manifest
from app.core.eseguibili.models import ExecutableManifest
from app.core.eseguibili.runtime import resolve_runtime_inputs
from app.core.eseguibili.service import resolve_runtime_connection_id
from app.core.eseguibili.upstream_plan import UPSTREAM_SUMMARY_KEY, plan_isolated_upstreams, split_upstream_inputs
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.projects.upstream_identity import ClosureEntry, identity_closure, upstream_identity_nodes
from app.shared.execution.runtime_bound_fields import RUNTIME_BOUND_REGISTRY, runtime_bound_context
from app.shared.execution.runtime_identity import RuntimeIdentity, identity_input_nodes, subject_for_test_run
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
    closure: Optional[Mapping[str, ClosureEntry]] = None,
) -> PipelineExecutionContract:
    """Congela canvas e connessione risolti prima dell'invio al worker.

    ``identity`` e' obbligatoria: «Esegui» e la stima del progetto passano
    ``RuntimeIdentity.project_run()`` (un input «Utente che esegue» blocca),
    anteprima e Valida l'identita' di prova di :func:`editor_identity`.

    Progetti a monte con «Utente che esegue» (runtime_identity U2): dopo gli
    input del progetto, il piano dei ricalcoli isolati con la STESSA
    identita' (``_with_upstream_plan``); senza identita' («Esegui») si blocca
    qui, subito. ``closure``: chiusura gia' letta (prova).
    """

    raw_inputs, upstream_values = split_upstream_inputs(raw_inputs)
    resolved = _apply_own_runtime_inputs(execution_contract, raw_inputs, identity=identity)
    return _with_upstream_plan(resolved, identity, upstream_values, closure=closure)


def _with_upstream_plan(
    execution_contract: PipelineExecutionContract,
    identity: RuntimeIdentity,
    upstream_values: Mapping[str, Mapping[str, object]],
    *,
    closure: Optional[Mapping[str, ClosureEntry]] = None,
) -> PipelineExecutionContract:
    plan = plan_isolated_upstreams(
        canvas=execution_contract.canvas,
        reader_type=execution_contract.project_type,
        group_id=execution_contract.group_id,
        identity=identity,
        upstream_values=upstream_values,
        closure=closure,
    )
    if not plan.isolated:
        return execution_contract
    context = dict(execution_contract.runtime_context)
    summary = dict(context.get("runtime_input_summary") or {})
    summary[UPSTREAM_SUMMARY_KEY] = plan.summary
    context["runtime_input_summary"] = summary
    return execution_contract.model_copy(update={"isolated_upstreams": plan.isolated, "runtime_context": context})


def _apply_own_runtime_inputs(
    execution_contract: PipelineExecutionContract,
    raw_inputs: Mapping[str, object],
    *,
    identity: RuntimeIdentity,
) -> PipelineExecutionContract:
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


def register_test_run_owner(task_id: str, *, project_id: str, user_id: str, group_id: str) -> None:
    """Chi ha lanciato la prova possiede il suo risultato (stesso sidecar
    degli Eseguibili, nello spazio dell'attivita'): solo lui lo scarica."""
    _output_store.save_task_owner(
        task_id, project_id, user_id, group_id=group_id, outputs_dir=settings.ESEGUIBILI_OUTPUTS_DIR,
    )


class ProjectTestRunNotApplicable(ValueError):
    """«Esegui di prova» su un progetto senza input «Utente che esegue»."""



def apply_test_run_inputs(
    execution_contract: PipelineExecutionContract,
    raw_inputs: Mapping[str, object],
) -> PipelineExecutionContract:
    """«Esegui di prova» dall'editor (``runtime_identity``, regola H).

    ``execution_contract`` e' costruito dal canvas SALVATO (chi chiama lo
    legge dal server): lo username di prova viene da li', mai dal client.
    Gli input si risolvono come in «Esegui» (stessa funzione, stessi errori)
    con l'identita' di prova; il contratto porta il soggetto ``test_run``,
    che isola il risultato (``PipelineExecutionContract.isolated_output``).
    Un progetto senza input «Utente che esegue» non ha nulla da provare: si
    esegue con «Esegui».
    """

    # Anche un progetto che legge, a monte, un input «Utente che esegue»
    # (runtime_identity U2): lo username di prova e' UNO per tutti i nodi.
    identity, closure = editor_identity(
        execution_contract.canvas, group_id=execution_contract.group_id, origin="test_run",
    )
    if not identity_input_nodes(execution_contract.canvas) and not closure:
        raise ProjectTestRunNotApplicable(
            "L'esecuzione di prova serve ai progetti con un input «Utente che esegue» "
            "(anche in un progetto a monte): questo progetto si esegue con «Esegui»."
        )
    # Senza un nome di prova valido si blocca nella risoluzione del progetto
    # o, se il progetto non ha input propri, nel piano a monte (stesso motivo).
    resolved = apply_project_runtime_inputs(execution_contract, raw_inputs, identity=identity, closure=closure)
    return resolved.model_copy(update={"execution_subject": subject_for_test_run(identity)})


def editor_identity(
    canvas: object, *, group_id: Optional[str], origin: str = "preview", node_id: object = None,
) -> tuple[RuntimeIdentity, Mapping[str, ClosureEntry]]:
    """L'UNICA costruzione dell'identita' di prova dell'editor (correzioni
    minori 11/11/2026, A4): anteprima, Valida con input, valori distinti,
    stima di un nodo (``origin="preview"``) e «Esegui di prova»
    (``origin="test_run"``, canvas SALVATO). Lo username di prova si legge
    dai nodi del progetto E da quelli dei progetti a monte che ne hanno
    bisogno (la chiusura delle sorgenti che l'esecuzione legge, ``node_id`` =
    passo dell'anteprima): un Manipola senza input proprio che legge un
    Estrai con «Utente che esegue» ha il suo utente di prova. Restituisce
    anche la chiusura (il piano a monte la riusa)."""
    closure = identity_closure(canvas, group_id=group_id, node_id=node_id)
    upstream_nodes = upstream_identity_nodes(closure)
    if origin == "test_run":
        return RuntimeIdentity.test_run(canvas, upstream_nodes=upstream_nodes), closure
    if origin != "preview":
        raise ValueError(f"origine dell'identita' di prova non valida: {origin}")
    return RuntimeIdentity.preview_of(canvas, upstream_nodes=upstream_nodes), closure


def apply_preview_inputs(
    execution_contract: PipelineExecutionContract, *, node_id: object = None,
) -> PipelineExecutionContract:
    """Anteprima dell'editor (``GET /pipeline/{id}/preview``): gli input
    «Utente che esegue» del progetto (:func:`apply_identity_inputs`) e il
    piano dei progetti a monte che ne hanno bisogno, con lo stesso username
    di prova (runtime_identity U2). Mai per un risultato salvato.
    ``node_id``: anteprima di un passo (la chiusura segue i suoi antenati)."""
    identity, closure = editor_identity(
        execution_contract.canvas, group_id=execution_contract.group_id, node_id=node_id,
    )
    resolved = apply_identity_inputs(execution_contract, identity)
    return _with_upstream_plan(resolved, identity, {}, closure=closure)
