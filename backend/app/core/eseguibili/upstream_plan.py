"""Piano dei ricalcoli isolati a monte («Utente che esegue», U2).

Regola in ``app/shared/execution/runtime_identity`` (sezione «UTENTE CHE
ESEGUE NEGLI ESTRAI A MONTE»). Qui c'e' l'UNICO punto che la applica
all'avvio, nel server (API), per ogni percorso che ha un'identita':

- avvio e stima degli Eseguibili (``service.execute_versioned``,
  ``preflight``): identita' ``authenticated``;
- «Esegui di prova» e anteprima dell'editor
  (``orchestrator.runtime_inputs``): identita' ``test_run`` / ``preview``;
- «Esegui» del progetto: identita' assente -> blocco subito (lo stesso blocco
  lo rifa' la porta nel worker per Schedule, catene, Carica).

Ogni progetto della chiusura (``projects.upstream_identity.identity_closure``)
si risolve sul suo canvas VIVO con la STESSA identita' e con la PROPRIA
definizione degli input (``compile_manifest`` + ``resolve_runtime_inputs``,
come il suo «Esegui»: trasformazione, valore scelto, segno ``value_literal``,
solo ``=``/``IN``). Il canvas risolto viaggia nel contratto
(``PipelineExecutionContract.isolated_upstreams``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.projects.upstream_identity import ClosureEntry, identity_closure, upstream_input_rows
from app.shared import error_codes
from app.shared.contracts.payloads import JsonObject
from app.shared.execution.runtime_bound_fields import RUNTIME_BOUND_REGISTRY, runtime_bound_registry
from app.shared.execution.runtime_identity import (
    KIND_LABEL,
    UPSTREAM_IDENTITY_ORIGINS,
    RuntimeIdentity,
    RuntimeIdentityError,
    missing_identity_error,
    split_upstream_input_key,
    upstream_identity_error,
)

from .manifest import compile_manifest
from .runtime import resolve_runtime_inputs

# Progetti a monte che si ricalcolano isolati (gli altri tipi bloccano, U5).
ISOLATED_PROJECT_TYPES = frozenset({"estrai", "manipola"})
# Lettori che hanno il ricalcolo isolato (Arricchisci no, U5).
ISOLATED_READER_TYPES = frozenset({"manipola"})
# Chiave del riepilogo della run con l'audit degli input a monte (non e'
# mai una chiave di input: queste non hanno «:»).
UPSTREAM_SUMMARY_KEY = "::a_monte"


@dataclass(frozen=True)
class UpstreamPlan:
    """Il piano: ``isolated`` va nel contratto, ``summary`` nell'audit."""

    isolated: JsonObject = field(default_factory=dict)
    summary: JsonObject = field(default_factory=dict)
    closure: Mapping[str, ClosureEntry] = field(default_factory=dict)


def split_upstream_inputs(raw_inputs: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Mapping[str, Any]]]:
    """``(input del progetto, {progetto a monte: {input: valore}})``."""
    own: dict[str, Any] = {}
    upstream: dict[str, dict[str, Any]] = {}
    for key, value in raw_inputs.items():
        parts = split_upstream_input_key(key)
        if parts is None:
            own[key] = value
        else:
            upstream.setdefault(parts[0], {})[parts[1]] = value
    return own, upstream


def _unsupported(entry: ClosureEntry, *, reader: bool) -> RuntimeIdentityError:
    need = entry.need
    what = (
        f"«{need.project_name}» legge un progetto a monte che dipende da «{KIND_LABEL}» "
        f"(input «{need.input_label}» di «{need.via[-1] if need.via else need.project_name}»)"
    )
    if reader:
        why = "Arricchisci non ricalcola i progetti a monte per l'utente che esegue"
    else:
        why = (
            f"il progetto a monte «{need.project_name}» e' un {entry.project.project_type or 'progetto'} e "
            "solo Estrai e Manipola si ricalcolano per l'utente che esegue"
        )
    return RuntimeIdentityError(
        error_codes.RUNTIME_IDENTITY_REQUIRED,
        f"{what}: {why}. L'esecuzione si ferma (il risultato salvato non e' filtrato). "
        "Sposta il filtro in un Estrai letto da un Manipola.",
    )


def _resolve_entry(
    pid: str, entry: ClosureEntry, *, identity: RuntimeIdentity, values: Mapping[str, Any],
) -> tuple[JsonObject, JsonObject]:
    project = entry.project
    manifest = compile_manifest(
        source_project_id=pid,
        project_type=project.project_type,  # type: ignore[arg-type]
        canvas=project.canvas,
        default_connection_id=project.default_connection_id,
    )
    # Come «Esegui» del progetto: i file del canvas restano quelli salvati.
    file_keys = {item.key for item in manifest.inputs if item.kind == "file"}
    manifest = manifest.model_copy(update={
        "inputs": [item for item in manifest.inputs if item.key not in file_keys],
        "bindings": [item for item in manifest.bindings if item.input_key not in file_keys],
    })
    resolved, cleanup_ids, summary = resolve_runtime_inputs(
        eseguibile_id=pid,
        canvas=project.canvas,
        manifest=manifest,
        values=values,
        group_id=str(project.group_id or ""),
        identity=identity.for_upstream(pid),
    )
    if cleanup_ids:  # nessun input file risolto qui (sopra): mai file temporanei
        raise ValueError(f"Progetto a monte «{project.name}»: input file non ammessi")
    from . import service as eseguibili_service  # evita il ciclo service <-> piano

    connection_id = eseguibili_service.resolve_runtime_connection_id(
        manifest, raw_inputs=values, group_id=str(project.group_id or ""),
    )
    plan_entry: JsonObject = {
        "project_type": project.project_type,
        "name": project.name,
        "canvas": resolved,
        "default_connection_id": connection_id,
    }
    registry = runtime_bound_registry(resolved)
    if registry:
        plan_entry[RUNTIME_BOUND_REGISTRY] = registry
    return plan_entry, {"name": project.name, "inputs": summary}


def plan_isolated_upstreams(
    *,
    canvas: Mapping[str, Any],
    reader_type: str,
    group_id: str,
    identity: RuntimeIdentity,
    upstream_values: Mapping[str, Mapping[str, Any]] | None = None,
    closure: Optional[Mapping[str, ClosureEntry]] = None,
) -> UpstreamPlan:
    """Il piano dell'esecuzione di ``canvas`` (letto da un ``reader_type``).

    Nessun progetto a monte con bisogno d'identita' -> piano vuoto (e un
    valore a monte del client si rifiuta). Altrimenti: identita' fuori da
    :data:`UPSTREAM_IDENTITY_ORIGINS` -> blocco; lettore o progetto a monte
    senza il ricalcolo isolato -> blocco; ogni progetto si risolve con la
    sua definizione."""
    upstream_values = dict(upstream_values or {})
    closure = dict(closure if closure is not None else identity_closure(canvas, group_id=group_id))
    declared = {(row["project_id"], row["input_key"]) for row in upstream_input_rows(closure)}
    unknown = sorted(
        f"{pid}::{key}" for pid, values in upstream_values.items() for key in values
        if (pid, key) not in declared
    )
    if unknown:
        raise ValueError("Input non dichiarati: " + ", ".join(unknown))
    if not closure:
        return UpstreamPlan()
    first = next(iter(closure.values()))
    if str(reader_type or "").lower() not in ISOLATED_READER_TYPES:
        raise _unsupported(first, reader=True)
    if identity.origin not in UPSTREAM_IDENTITY_ORIGINS or identity.username is None:
        if identity.origin == "absent" and identity.reason not in ("project_run", "unattended"):
            # Prova o anteprima senza un nome utente di prova valido: il
            # motivo della prova (stesso messaggio dei nodi del progetto).
            raise missing_identity_error(first.need.input_label, identity.reason)
        raise upstream_identity_error(first.need)
    isolated: JsonObject = {}
    summary: JsonObject = {}
    for pid, entry in closure.items():
        if entry.project.project_type not in ISOLATED_PROJECT_TYPES:
            raise _unsupported(entry, reader=False)
        isolated[pid], summary[pid] = _resolve_entry(
            pid, entry, identity=identity, values=upstream_values.get(pid, {}),
        )
    return UpstreamPlan(isolated=isolated, summary=summary, closure=closure)


# Snapshot alla pubblicazione (U7 della raccolta): gli input a monte con
# identita' al momento della pubblicazione. Togliere uno di questi dopo la
# pubblicazione allarga i dati: avvio e stima si fermano finche' non si
# ripubblica; aggiungerne uno restringe e vale subito.


def upstream_snapshot(closure: Mapping[str, ClosureEntry]) -> list[JsonObject]:
    """Righe dello snapshot (manifest ``upstream_identity``)."""
    return [
        {"project_id": row["project_id"], "project_name": row["project_name"],
         "input_key": row["input_key"], "label": row["label"]}
        for row in upstream_input_rows(closure)
    ]


def removed_upstream_inputs(snapshot: Sequence[Any], closure: Mapping[str, ClosureEntry]) -> list[JsonObject]:
    """Righe dello snapshot che la chiusura viva non ha piu'."""
    live = {(row["project_id"], row["input_key"]) for row in upstream_input_rows(closure)}
    removed = []
    for item in snapshot or []:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if (str(data.get("project_id")), str(data.get("input_key"))) not in live:
            removed.append(data)
    return removed


def assert_upstream_snapshot(snapshot: Sequence[Any], closure: Mapping[str, ClosureEntry]) -> None:
    """Blocco dell'avvio e della stima se un input a monte della versione
    pubblicata non c'e' piu' (i dati si allargherebbero in silenzio)."""
    removed = removed_upstream_inputs(snapshot, closure)
    if removed:
        names = ", ".join(f"«{row.get('label') or row.get('input_key')}» di «{row.get('project_name')}»"
                          for row in removed)
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_REQUIRED,
            f"Il progetto a monte non filtra piu' per «{KIND_LABEL}» ({names}): questo Eseguibile "
            "vedrebbe dati che alla pubblicazione erano filtrati. Chi gestisce l'Eseguibile deve "
            "ripubblicarlo per confermare.",
        )


def upstream_identity_changes(published_manifest: Any, closure: Mapping[str, ClosureEntry]) -> list[JsonObject]:
    """Righe ``upstream_identity_removed`` della ripubblicazione e di N1: gli
    input a monte della versione attiva che non ci sono piu' (bloccano:
    la nuova versione allargherebbe i dati)."""
    snapshot = published_manifest.get("upstream_identity") if isinstance(published_manifest, Mapping) else None
    return [
        {
            "input_key": f"{row.get('project_id')}::{row.get('input_key')}",
            "change": "upstream_identity_removed",
            "target_node_id": None,
            "target_field": None,
            "motivo": (
                f"Il progetto a monte «{row.get('project_name') or row.get('project_id')}» non filtra piu' "
                f"per «{KIND_LABEL}» (input «{row.get('label') or row.get('input_key')}»): chi esegue "
                "vedrebbe i dati di tutti"
            ),
            "blocking": True,
        }
        for row in removed_upstream_inputs(snapshot or [], closure)
    ]


def plan_for_version(
    *,
    canvas: Mapping[str, Any],
    manifest: Any,
    group_id: str,
    raw_inputs: Mapping[str, Any],
    identity: RuntimeIdentity,
) -> tuple[Mapping[str, Any], UpstreamPlan]:
    """Avvio e stima di un Eseguibile: ``(input del progetto, piano)``. Lo
    snapshot della pubblicazione si controlla prima del piano (U7)."""
    own, upstream = split_upstream_inputs(raw_inputs)
    closure = (
        identity_closure(canvas, group_id=group_id)
        if str(manifest.project_type) != "estrai" else {}
    )
    assert_upstream_snapshot(getattr(manifest, "upstream_identity", None) or [], closure)
    plan = plan_isolated_upstreams(
        canvas=canvas, reader_type=str(manifest.project_type), group_id=group_id,
        identity=identity, upstream_values=upstream, closure=closure,
    )
    return own, plan


__all__ = [
    "plan_for_version",
    "upstream_identity_changes",
    "ISOLATED_PROJECT_TYPES",
    "ISOLATED_READER_TYPES",
    "UPSTREAM_SUMMARY_KEY",
    "UpstreamPlan",
    "assert_upstream_snapshot",
    "plan_isolated_upstreams",
    "removed_upstream_inputs",
    "split_upstream_inputs",
    "upstream_snapshot",
]
