"""Progetti a monte che hanno bisogno dell'«Utente che esegue» (U1).

Regola in ``app/shared/execution/runtime_identity`` (sezione «UTENTE CHE
ESEGUE NEGLI ESTRAI A MONTE»). Qui c'e' l'UNICA lettura che la decide: la
chiusura transitiva sui canvas VIVI dei progetti a monte dello stesso gruppo.

- :func:`identity_closure`: per un canvas, ogni progetto a monte (anche
  indiretto) che ha un input «Utente che esegue» o ne legge uno, con il
  motivo (:class:`~app.shared.execution.runtime_identity.UpstreamIdentityNeed`).
- :func:`source_identity_need`: lo stesso per UN progetto a monte (i lettori
  lo chiamano prima della porta ``route_upstream_source``).
- :func:`annotate_canvas_upstream_identity`: annotazione transitoria dei nodi
  ``project_source`` per l'editor (come ``source_freshness``): l'editor sa che
  «Esegui» diventa la prova e quale utente di prova vale.

Raggiungibilita' (correzioni minori 11/11/2026, A5): si seguono SOLO i nodi
``project_source`` che l'esecuzione legge davvero, con la regola degli
esecutori (``canvas_preview_scope.executed_canvas``: rami che arrivano
all'Output, oppure il passo dell'anteprima e i suoi antenati), sia nel canvas
del lettore sia in quello di ogni progetto a monte; e senza le sorgenti che
un input dell'Eseguibile sostituisce (``replaced_node_ids``: le calcola
``eseguibili.upstream_plan.replaced_source_node_ids``). Un ramo scollegato
non blocca piu' nulla.

Un progetto di un altro gruppo non si segue (la sorgente e' gia' rifiutata da
chi la legge: tenant fail-closed). Una lettura una volta per progetto per
chiamata (memoria ``_Loader``).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Optional

from app.core.projects import db as project_db
from app.core.projects.canvas_preview_scope import executed_canvas
from app.shared.contracts.payloads import JsonObject
from app.shared.error_envelope import raise_typed_http
from app.shared.execution.runtime_identity import (
    KIND_LABEL,
    PREVIEW_ADMIN_VALUE_FIELD,
    PREVIEW_USERNAME_FIELD,
    SAVED_RESULT_EXEMPT_ROLES,
    RuntimeIdentityError,
    UpstreamIdentityNeed,
    route_saved_result,
    admin_override_enabled,
    admin_override_label,
    identity_input_nodes,
    identity_transform,
    upstream_input_key,
)
from app.shared.execution.runtime_input_kinds import node_value

UPSTREAM_IDENTITY_ANNOTATION = "upstream_identity"


@dataclass(frozen=True)
class UpstreamProject:
    """Il progetto a monte come lo legge la chiusura (canvas vivo)."""

    project_id: str
    name: str
    project_type: str
    group_id: Optional[str]
    canvas: JsonObject
    default_connection_id: Optional[str] = None


ProjectLoader = Callable[[str], Optional[UpstreamProject]]


def _as_canvas(raw: Any) -> JsonObject:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump()
    return dict(raw) if isinstance(raw, Mapping) else {}


def load_upstream_project(project_id: str, *, group_id: Optional[str]) -> Optional[UpstreamProject]:
    """Lettura del progetto a monte nello stesso gruppo (None se manca o e'
    di un altro gruppo)."""
    if not group_id:
        return None
    row = project_db.get_project_row(project_id, group_id=group_id)
    if row is None:
        return None
    return UpstreamProject(
        project_id=str(row.id),
        name=str(row.name or row.id),
        project_type=str(row.project_type or "").lower(),
        group_id=row.group_id,
        canvas=_as_canvas(row.canvas),
        default_connection_id=row.connection_id,
    )


def _nodes(canvas: Any) -> list[Any]:
    if isinstance(canvas, Mapping):
        return list(canvas.get("nodes") or [])
    return list(getattr(canvas, "nodes", None) or [])


def _node_id(node: Any) -> str:
    return str(node.get("id") if isinstance(node, Mapping) else getattr(node, "id", ""))


def source_project_ids(
    canvas: Any, *, node_id: Any = None, replaced_node_ids: frozenset[str] | set[str] = frozenset(),
) -> list[str]:
    """Id dei progetti letti dai nodi ``project_source`` che l'esecuzione
    legge davvero (ordine del canvas): la parte eseguita
    (``executed_canvas``, ``node_id`` = passo dell'anteprima) senza i nodi
    sostituiti da un input (``replaced_node_ids``)."""
    seen: list[str] = []
    for node in _nodes(executed_canvas(canvas, node_id)):
        node_type = node.get("type") if isinstance(node, Mapping) else getattr(node, "type", None)
        if node_type != "project_source" or _node_id(node) in replaced_node_ids:
            continue
        source_id = str(node_value(node, "source_project_id") or "").strip()
        if source_id and source_id not in seen:
            seen.append(source_id)
    return seen


class _Loader:
    """Una lettura per progetto per chiamata."""

    def __init__(self, group_id: Optional[str], load: Optional[ProjectLoader]) -> None:
        self._load = load or (lambda pid: load_upstream_project(pid, group_id=group_id))
        self._cache: dict[str, Optional[UpstreamProject]] = {}

    def __call__(self, project_id: str) -> Optional[UpstreamProject]:
        if project_id not in self._cache:
            self._cache[project_id] = self._load(project_id)
        return self._cache[project_id]


def _need_of(project_id: str, loader: _Loader, visiting: tuple[str, ...]) -> Optional[UpstreamIdentityNeed]:
    project = loader(project_id)
    if project is None or project_id in visiting:
        # Progetto assente o di un altro gruppo (lo rifiuta chi lo legge) e
        # cicli (li rifiuta il controllo della catena): niente da seguire.
        return None
    own = identity_input_nodes(project.canvas)
    if own:
        label = str(node_value(own[0], "label") or node_value(own[0], "input_key") or KIND_LABEL)
        return UpstreamIdentityNeed(project.project_id, project.name, project.project_type, (project.name,), label)
    for source_id in source_project_ids(project.canvas):
        inner = _need_of(source_id, loader, (*visiting, project_id))
        if inner is not None:
            return UpstreamIdentityNeed(
                project.project_id, project.name, project.project_type, (project.name, *inner.via),
                inner.input_label,
            )
    return None


def source_identity_need(
    source_project_id: str, *, group_id: Optional[str], load: Optional[ProjectLoader] = None,
) -> Optional[UpstreamIdentityNeed]:
    """Il progetto a monte ``source_project_id`` ha bisogno dell'identita'?"""
    return _need_of(str(source_project_id), _Loader(group_id, load), ())


def assert_saved_result_available(project_id: str, current_user: Any) -> None:
    """Porta U6 del risultato SALVATO di ``project_id`` usato fuori da
    un'esecuzione (download del progetto, di un suo task, di una
    Pianificazione; pubblicazione Feature Service): se il progetto ha bisogno
    dell'identita' risponde 409 «questo progetto filtra per utente: usa
    l'Eseguibile» (salvo il superadmin, ``SAVED_RESULT_EXEMPT_ROLES``).
    Chi chiama ha gia' verificato il gruppo (il progetto e' del gruppo
    dell'utente). Va chiamata PRIMA di leggere (test di enumerazione)."""
    role = getattr(current_user, "role", None)
    if role in SAVED_RESULT_EXEMPT_ROLES:
        return
    try:
        route_saved_result(
            need=source_identity_need(str(project_id), group_id=getattr(current_user, "group_id", None)),
            role=role,
        )
    except RuntimeIdentityError as exc:
        raise_typed_http(exc.code, status_code=409, message=exc.message)


@dataclass(frozen=True)
class ClosureEntry:
    """Un progetto della chiusura: il progetto, il motivo, i suoi input
    «Utente che esegue» (vuoti se ne ha bisogno solo indirettamente)."""

    project: UpstreamProject
    need: UpstreamIdentityNeed
    identity_nodes: tuple[Any, ...]


def identity_closure(
    canvas: Any,
    *,
    group_id: Optional[str],
    load: Optional[ProjectLoader] = None,
    node_id: Any = None,
    replaced_node_ids: frozenset[str] | set[str] = frozenset(),
) -> Mapping[str, ClosureEntry]:
    """Ogni progetto a monte di ``canvas`` (anche indiretto) che ha bisogno
    dell'identita', in ordine di visita. Un progetto che NON ne ha bisogno
    non c'e' (si legge come sempre). Si seguono solo le sorgenti lette
    (:func:`source_project_ids`: ``node_id`` = passo dell'anteprima,
    ``replaced_node_ids`` = sorgenti sostituite da un input)."""
    loader = _Loader(group_id, load)
    closure: dict[str, ClosureEntry] = {}

    def visit(project_id: str, visiting: tuple[str, ...]) -> None:
        if project_id in closure or project_id in visiting:
            return
        need = _need_of(project_id, loader, visiting)
        if need is None:
            return
        project = loader(project_id)
        assert project is not None  # _need_of ha gia' letto il progetto
        closure[project_id] = ClosureEntry(project, need, tuple(identity_input_nodes(project.canvas)))
        for source_id in source_project_ids(project.canvas):
            visit(source_id, (*visiting, project_id))

    for source_id in source_project_ids(canvas, node_id=node_id, replaced_node_ids=replaced_node_ids):
        visit(source_id, ())
    return closure


def cached_loader(group_id: Optional[str]) -> ProjectLoader:
    """Un caricatore con memoria da passare a piu' chiusure della stessa
    chiamata (una lettura per progetto anche con due chiusure)."""
    return _Loader(group_id, None)


def upstream_identity_nodes(closure: Mapping[str, ClosureEntry]) -> list[tuple[str, Any]]:
    """``(id progetto, nodo)`` degli input «Utente che esegue» della chiusura
    (username di prova unico, ``RuntimeIdentity.test_run(upstream_nodes=)``)."""
    return [(pid, node) for pid, entry in closure.items() for node in entry.identity_nodes]


def upstream_input_rows(closure: Mapping[str, ClosureEntry], *, editor: bool = False) -> list[JsonObject]:
    """Gli input a monte come li mostra chi avvia (form dell'Eseguibile,
    annotazione dell'editor): chiave ``progetto::input`` e opzioni del nodo.
    ``editor``: anche lo username di prova (solo per chi modifica)."""
    rows: list[JsonObject] = []
    for pid, entry in closure.items():
        for node in entry.identity_nodes:
            key = str(node_value(node, "input_key") or "")
            if not key:
                continue
            row: JsonObject = {
                "key": upstream_input_key(pid, key),
                "input_key": key,
                "project_id": pid,
                "project_name": entry.project.name,
                "label": str(node_value(node, "label") or key),
                "kind": str(node_value(node, "input_kind") or ""),
                "transform": identity_transform(node),
                "admin_override": admin_override_enabled(node),
                "admin_override_label": admin_override_label(node),
            }
            if editor:
                row[PREVIEW_USERNAME_FIELD] = str(node_value(node, PREVIEW_USERNAME_FIELD) or "").strip() or None
                row["has_preview_admin_value"] = node_value(node, PREVIEW_ADMIN_VALUE_FIELD) is not None
            rows.append(row)
    return rows


def annotate_canvas_upstream_identity(canvas: Any, *, group_id: Optional[str]) -> None:
    """Annotazione transitoria dei nodi ``project_source`` (solo editor; il
    server non la rilegge mai: la toglie il salvataggio)."""
    for node in _nodes(canvas):
        if getattr(node, "type", None) != "project_source":
            continue
        source_id = str(node_value(node, "source_project_id") or "").strip()
        payload: Optional[JsonObject] = None
        if source_id:
            sub = {"nodes": [{"type": "project_source", "data": {"source_project_id": source_id}}]}
            closure = identity_closure(sub, group_id=group_id)
            if source_id in closure:
                need = closure[source_id].need
                payload = {
                    "via": list(need.via),
                    "input_label": need.input_label,
                    "inputs": upstream_input_rows(closure, editor=True),
                }
        if getattr(node, "data", None) is None:
            node.data = {}
        if isinstance(node.data, dict):
            if payload is None:
                node.data.pop(UPSTREAM_IDENTITY_ANNOTATION, None)
            else:
                node.data[UPSTREAM_IDENTITY_ANNOTATION] = payload


def clear_canvas_upstream_identity(canvas: Any) -> None:
    """Toglie l'annotazione prima di salvare."""
    for node in _nodes(canvas):
        if isinstance(node, Mapping):
            data = node.get("data")
        else:
            if getattr(node, "type", None) != "project_source":
                continue
            data = getattr(node, "data", None)
        if isinstance(data, dict):
            data.pop(UPSTREAM_IDENTITY_ANNOTATION, None)


__all__ = [
    "ClosureEntry",
    "UPSTREAM_IDENTITY_ANNOTATION",
    "UpstreamProject",
    "annotate_canvas_upstream_identity",
    "assert_saved_result_available",
    "cached_loader",
    "clear_canvas_upstream_identity",
    "identity_closure",
    "load_upstream_project",
    "source_identity_need",
    "source_project_ids",
    "upstream_identity_nodes",
    "upstream_input_rows",
]
