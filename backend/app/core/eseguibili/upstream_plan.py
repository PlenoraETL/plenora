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

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.projects.upstream_identity import ClosureEntry, cached_loader, identity_closure, upstream_input_rows
from app.shared import error_codes
from app.shared.contracts.payloads import JsonObject
from app.shared.execution.runtime_bound_fields import RUNTIME_BOUND_REGISTRY, runtime_bound_registry
from app.shared.execution.runtime_input_kinds import is_server_injected
from app.utils.logger import get_logger
from app.shared.execution.runtime_identity import (
    KIND_LABEL,
    UPSTREAM_IDENTITY_ORIGINS,
    RuntimeIdentity,
    RuntimeIdentityError,
    missing_identity_error,
    split_upstream_input_key,
    upstream_identity_error,
)

from . import db as eseguibili_db
from .manifest import compile_manifest
from .models import UpstreamIdentityInput
from .runtime import resolve_runtime_inputs

logger = get_logger(__name__)

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
#
# Correzioni minori (11/11/2026). UNA chiusura per l'Eseguibile
# (:func:`executable_closure`): le sorgenti che l'esecuzione legge davvero
# (A5: rami che arrivano all'Output, senza le sorgenti sostituite da un input
# dell'Eseguibile). Le stesse funzioni per pubblicazione, ripubblicazione,
# avvio, stima, dettaglio (N1) e lista (A6, A7):
# - lo snapshot si costruisce col modello validato (A3, :func:`upstream_snapshot`);
# - una versione pubblicata prima dello snapshot (manifest senza la chiave)
#   riceve UNA volta lo snapshot = chiusura attuale della sua versione (A2,
#   :func:`version_snapshot`, backfill idempotente): da li' in poi togliere
#   un input a monte blocca come per le altre;
# - «avvio bloccato» = snapshot contro la chiusura della VERSIONE pubblicata
#   (:func:`launch_removed_inputs`, la stessa dell'avvio); «la ripubblicazione
#   allargherebbe» = snapshot contro la chiusura del canvas VIVO
#   (:func:`republish_removed_inputs`, la stessa della ripubblicazione).

SNAPSHOT_FIELD = "upstream_identity"


def _file_provided(definition: Any, raw_inputs: Mapping[str, Any]) -> bool:
    """Stessa regola di ``runtime.resolve_runtime_inputs``: nessun file =
    assente, null o lista vuota."""
    value = raw_inputs.get(definition.key, definition.default_value)
    return not (value is None or (isinstance(value, list) and not value))


def replaced_source_node_ids(
    inputs: Sequence[Any], bindings: Sequence[Any], raw_inputs: Optional[Mapping[str, Any]] = None,
) -> frozenset[str]:
    """Nodi sorgente che un input dell'Eseguibile sostituisce (collegamento
    ``data_source``), quindi mai letti: «Utente che esegue» come sorgente
    sempre; un file se e' obbligatorio o, all'avvio (``raw_inputs``), se il
    file c'e'. Un file facoltativo senza ``raw_inputs`` (pubblicazione,
    controllo dello snapshot) NON sostituisce: la sorgente puo' essere letta."""
    by_key = {item.key: item for item in inputs}
    replaced: set[str] = set()
    for binding in bindings:
        if binding.mode != "data_source":
            continue
        definition = by_key.get(binding.input_key)
        if definition is None:
            continue
        if is_server_injected(definition.kind):
            replaced.add(str(binding.target_node_id))
        elif definition.kind == "file" and (
            definition.required if raw_inputs is None else _file_provided(definition, raw_inputs)
        ):
            replaced.add(str(binding.target_node_id))
    return frozenset(replaced)


def executable_closure(
    *,
    canvas: Any,
    project_type: Any,
    inputs: Sequence[Any] = (),
    bindings: Sequence[Any] = (),
    group_id: str,
    raw_inputs: Optional[Mapping[str, Any]] = None,
    load: Any = None,
) -> Mapping[str, ClosureEntry]:
    """La chiusura a monte di un Eseguibile (o di una versione da
    pubblicare): un Estrai non ha progetti a monte."""
    if str(project_type or "").lower() == "estrai" or not isinstance(canvas, Mapping):
        return {}
    return identity_closure(
        canvas, group_id=group_id, load=load,
        replaced_node_ids=replaced_source_node_ids(inputs, bindings, raw_inputs),
    )


def upstream_snapshot(closure: Mapping[str, ClosureEntry]) -> list[UpstreamIdentityInput]:
    """Righe dello snapshot (manifest ``upstream_identity``), validate e
    normalizzate dal modello (``UPSTREAM_IDENTITY_LIMITS``)."""
    return [
        UpstreamIdentityInput.model_validate({
            "project_id": row["project_id"], "project_name": row["project_name"],
            "input_key": row["input_key"], "label": row["label"],
        })
        for row in upstream_input_rows(closure)
    ]


def _snapshot_key(item: Any) -> tuple[str, str]:
    data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
    row = UpstreamIdentityInput.model_validate({
        "project_id": data.get("project_id"), "input_key": data.get("input_key"),
        "project_name": data.get("project_name") or "", "label": data.get("label") or "",
    })
    return row.project_id, row.input_key


def removed_upstream_inputs(snapshot: Sequence[Any], closure: Mapping[str, ClosureEntry]) -> list[JsonObject]:
    """Righe dello snapshot che la chiusura non ha piu' (chiavi normalizzate
    da entrambe le parti)."""
    live = {_snapshot_key(row) for row in upstream_snapshot(closure)}
    removed = []
    for item in snapshot or []:
        data = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        if _snapshot_key(data) not in live:
            removed.append(data)
    return removed


def version_snapshot(version: Any, manifest: Any, *, group_id: str, load: Any = None) -> list[UpstreamIdentityInput]:
    """Lo snapshot della versione. Versione pubblicata prima dello snapshot
    (il manifest salvato non ha la chiave): lo snapshot e' la chiusura
    attuale della versione, scritto una volta (backfill idempotente; se la
    scrittura non riesce vale comunque per questa lettura). Un input tolto
    PRIMA del primo backfill non si puo' vedere (nessuna storia: dichiarato)."""
    raw = getattr(version, "manifest", None)
    if isinstance(raw, Mapping) and SNAPSHOT_FIELD in raw:
        return list(getattr(manifest, "upstream_identity", None) or [])
    closure = executable_closure(
        canvas=getattr(version, "canvas", None), project_type=getattr(manifest, "project_type", ""),
        inputs=getattr(manifest, "inputs", ()), bindings=getattr(manifest, "bindings", ()),
        group_id=group_id, load=load,
    )
    snapshot = upstream_snapshot(closure)
    version_id = getattr(version, "id", None)
    if version_id and snapshot:
        try:
            eseguibili_db.backfill_version_upstream_identity(
                str(version_id), [row.model_dump(mode="json") for row in snapshot],
            )
        except Exception:  # best effort dichiarato: lo snapshot vale comunque per questa lettura
            logger.warning(f"Snapshot a monte della versione {version_id}: scrittura non riuscita", exc_info=True)
    return snapshot


def launch_removed_inputs(
    *, version: Any, manifest: Any, group_id: str, load: Any = None,
    closure: Optional[Mapping[str, ClosureEntry]] = None,
) -> list[JsonObject]:
    """Input a monte dello snapshot che la chiusura della VERSIONE pubblicata
    non ha piu': avvio e stima si fermano (stessa funzione per avvio, stima,
    N1 e lista). ``closure``: chiusura della versione gia' letta."""
    snapshot = version_snapshot(version, manifest, group_id=group_id, load=load)
    if not snapshot:
        return []
    if closure is None:
        closure = executable_closure(
            canvas=version.canvas, project_type=manifest.project_type,
            inputs=manifest.inputs, bindings=manifest.bindings, group_id=group_id, load=load,
        )
    return removed_upstream_inputs(snapshot, closure)


def republish_removed_inputs(
    *, version: Any, manifest: Any, group_id: str, live_closure: Mapping[str, ClosureEntry], load: Any = None,
) -> list[JsonObject]:
    """Input a monte dello snapshot che la nuova versione (chiusura del
    canvas VIVO) non avrebbe piu': la ripubblicazione chiede conferma."""
    return removed_upstream_inputs(version_snapshot(version, manifest, group_id=group_id, load=load), live_closure)


def raise_if_removed(removed: Sequence[Mapping[str, Any]]) -> None:
    """Blocco dell'avvio e della stima se un input a monte della versione
    pubblicata non c'e' piu' (i dati si allargherebbero in silenzio)."""
    if removed:
        names = ", ".join(f"«{row.get('label') or row.get('input_key')}» di «{row.get('project_name')}»"
                          for row in removed)
        raise RuntimeIdentityError(
            error_codes.RUNTIME_IDENTITY_REQUIRED,
            f"Il progetto a monte non filtra piu' per «{KIND_LABEL}» ({names}): questo Eseguibile "
            "vedrebbe dati che alla pubblicazione erano filtrati. Chi gestisce l'Eseguibile deve "
            "ripubblicarlo per confermare.",
        )


def assert_upstream_snapshot(snapshot: Sequence[Any], closure: Mapping[str, ClosureEntry]) -> None:
    """:func:`raise_if_removed` su uno snapshot e una chiusura dati."""
    raise_if_removed(removed_upstream_inputs(snapshot, closure))


def upstream_identity_changes(removed: Sequence[Mapping[str, Any]], *, launch: bool = False) -> list[JsonObject]:
    """Righe ``upstream_identity_removed`` (ripubblicazione e N1) dalle righe
    tolte. ``launch``: tolte dalla versione pubblicata (l'avvio e' gia'
    fermo); altrimenti dalla nuova versione (la ripubblicazione allargherebbe)."""
    rows = []
    for row in removed:
        who = row.get("project_name") or row.get("project_id")
        what = row.get("label") or row.get("input_key")
        motivo = (
            f"Il progetto a monte «{who}» non filtra piu' per «{KIND_LABEL}» (input «{what}»): l'avvio "
            "e' fermo finche' non si ripubblica (chi esegue vedrebbe i dati di tutti)"
            if launch else
            f"La nuova versione non filtrerebbe piu' per «{KIND_LABEL}» con l'input «{what}» di «{who}» "
            "(input tolto o progetto non piu' letto): chi esegue potrebbe vedere i dati di tutti"
        )
        rows.append({
            "input_key": f"{row.get('project_id')}::{row.get('input_key')}",
            "change": "upstream_identity_removed",
            "target_node_id": None,
            "target_field": None,
            "motivo": motivo,
            "blocking": True,
        })
    return rows


def merge_upstream_changes(*groups: Sequence[JsonObject]) -> list[JsonObject]:
    """Righe di piu' confronti, una per input (vince la prima)."""
    seen: set[str] = set()
    merged = []
    for group in groups:
        for row in group:
            if row["input_key"] not in seen:
                seen.add(row["input_key"])
                merged.append(row)
    return merged


# B6 (correzioni minori 11/11/2026): gruppi degli input «Utente che esegue».
# Gli input a monte (e quelli dell'Eseguibile stesso) con la STESSA firma
# (:func:`group_signature`: «gli amministratori possono scegliere»,
# trasformazione, etichetta del campo) sono UN SOLO campo per chi avvia: il
# server raggruppa (:func:`identity_groups`), mai il client. Chiave stabile
# :func:`group_key` (``@gruppo::<hash della firma>``; ``@`` non e' mai in un
# id progetto). Il valore del campo vale per ogni input del gruppo
# (:func:`expand_group_values`); le chiavi singole restano valide (prevalgono
# sul valore del gruppo per quell'input). ``may_choose`` resta per OGNI input
# (il valore arriva a ognuno con la sua chiave); l'audit elenca gli input a
# cui il valore del gruppo e' stato applicato (:data:`GROUPS_SUMMARY_KEY`).
GROUP_KEY_PREFIX = "@gruppo" + "::"
GROUPS_SUMMARY_KEY = "@gruppi"


def group_signature(row: Mapping[str, Any]) -> tuple[bool, str, str]:
    """Firma del gruppo: stessa scelta degli amministratori, trasformazione
    ed etichetta del campo (quella per gli amministratori, se c'e')."""
    admin = bool(row.get("admin_override"))
    label = str((row.get("admin_override_label") if admin else None) or row.get("label") or "")
    return admin, str(row.get("transform") or ""), label


def group_key(signature: tuple[bool, str, str]) -> str:
    digest = hashlib.sha256(json.dumps(list(signature), ensure_ascii=False).encode("utf-8")).hexdigest()[:12]
    return f"{GROUP_KEY_PREFIX}{digest}"


def own_identity_rows(inputs: Sequence[Any]) -> list[JsonObject]:
    """Gli input «Utente che esegue» dell'Eseguibile stesso, come righe."""
    return [
        {
            "key": item.key, "input_key": item.key, "project_id": None, "project_name": "",
            "label": item.label, "kind": item.kind, "transform": item.transform,
            "admin_override": bool(item.admin_override), "admin_override_label": item.admin_override_label,
        }
        for item in inputs if is_server_injected(item.kind)
    ]


def identity_groups(closure: Mapping[str, ClosureEntry], own_inputs: Sequence[Any] = ()) -> list[JsonObject]:
    """UNA riga per gruppo che contiene almeno un input a monte (un gruppo di
    soli input propri e' il campo dell'Eseguibile di sempre). ``members``:
    le chiavi di tutti gli input del gruppo (``progetto::input`` o la chiave
    dell'input proprio)."""
    by_signature: dict[tuple[bool, str, str], list[JsonObject]] = {}
    for row in [*own_identity_rows(own_inputs), *upstream_input_rows(closure)]:
        by_signature.setdefault(group_signature(row), []).append(row)
    groups: list[JsonObject] = []
    for signature, rows in by_signature.items():
        upstream_rows = [row for row in rows if row.get("project_id")]
        if not upstream_rows:
            continue
        first = upstream_rows[0]
        names = list(dict.fromkeys(str(row["project_name"]) for row in upstream_rows))
        groups.append({
            **first,
            "key": group_key(signature),
            "input_key": group_key(signature),
            "project_name": ", ".join(names),
            "label": str(first.get("label") or ""),
            "members": [str(row["key"]) for row in rows],
        })
    return groups


def expand_group_values(raw_inputs: Mapping[str, Any], groups: Sequence[Mapping[str, Any]]) -> tuple[JsonObject, JsonObject]:
    """``(input con i valori dei gruppi applicati a ogni membro, audit)``. Una
    chiave singola gia' presente prevale; una chiave di gruppo sconosciuta
    resta (si rifiuta come input non dichiarato)."""
    by_key = {str(group["key"]): group for group in groups}
    expanded: JsonObject = {key: value for key, value in raw_inputs.items() if key not in by_key}
    audit: JsonObject = {}
    for key, value in raw_inputs.items():
        group = by_key.get(key)
        if group is None:
            continue
        applied = [member for member in group["members"] if member not in raw_inputs]
        for member in applied:
            expanded[member] = value
        audit[key] = {"label": group.get("admin_override_label") or group.get("label"), "inputs": applied}
    return expanded, audit


def plan_for_version(
    *,
    version: Any,
    manifest: Any,
    group_id: str,
    raw_inputs: Mapping[str, Any],
    identity: RuntimeIdentity,
) -> tuple[Mapping[str, Any], UpstreamPlan]:
    """Avvio e stima di un Eseguibile: ``(input del progetto, piano)``. Lo
    snapshot della pubblicazione si controlla prima del piano (U7) sulla
    chiusura della versione; il piano usa la chiusura di QUESTO avvio (un file
    dato sostituisce la sua sorgente, A5)."""
    loader = cached_loader(group_id)
    common: dict[str, Any] = dict(
        canvas=version.canvas, project_type=manifest.project_type, inputs=manifest.inputs,
        bindings=manifest.bindings, group_id=group_id, load=loader,
    )
    published = executable_closure(**common)
    raise_if_removed(launch_removed_inputs(
        version=version, manifest=manifest, group_id=group_id, load=loader, closure=published,
    ))
    # B6: il valore di un campo di gruppo vale per ogni suo input (anche quello
    # proprio), prima di separare gli input propri da quelli a monte.
    expanded, group_audit = expand_group_values(raw_inputs, identity_groups(published, manifest.inputs))
    own, upstream = split_upstream_inputs(expanded)
    closure = executable_closure(**common, raw_inputs=own)
    # Valori per un progetto a monte che questo avvio non legge (sorgente
    # sostituita da un file): dichiarati, non usati.
    upstream = {pid: values for pid, values in upstream.items() if pid in closure or pid not in published}
    plan = plan_isolated_upstreams(
        canvas=version.canvas, reader_type=str(manifest.project_type), group_id=group_id,
        identity=identity, upstream_values=upstream, closure=closure,
    )
    if group_audit:
        plan = UpstreamPlan(isolated=plan.isolated, summary={**plan.summary, GROUPS_SUMMARY_KEY: group_audit},
                            closure=plan.closure)
    return own, plan


__all__ = [
    "SNAPSHOT_FIELD",
    "GROUP_KEY_PREFIX",
    "GROUPS_SUMMARY_KEY",
    "expand_group_values",
    "group_key",
    "group_signature",
    "identity_groups",
    "own_identity_rows",
    "executable_closure",
    "launch_removed_inputs",
    "merge_upstream_changes",
    "plan_for_version",
    "raise_if_removed",
    "replaced_source_node_ids",
    "republish_removed_inputs",
    "upstream_identity_changes",
    "version_snapshot",
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
