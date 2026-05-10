"""
Router API per il modulo Projects.

Endpoint CRUD progetti, gestione canvas, e inferenza schema.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, List, Optional

from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.observability.audit_action import audit_action
from app.core.projects.canvas_models import CanvasState, Node, Edge
from app.core.projects.exceptions import (
    CanvasCorruptedError,
    ProjectCleanupPartialError,
    ProjectHasDependenciesError,
    ProjectLineageCycleError,
)
from app.core.auth.dependencies import (
    require_not_esecutore,
    require_user_or_above,
    require_superadmin,
)
from app.core.auth.owner_dependencies import require_project_owner
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import assert_group_access
from app.core.projects.usage import list_owned_project_ids
from .models import ProjectCreate

class ProjectCreateWithCanvas(ProjectCreate):
    canvas: Any = None
    plugins: Any = None


logger = get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

_project_service = None
_schema_service = None
_validator = None


def init_projects(project_service: Any) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _project_service, _schema_service, _validator
    _project_service = project_service
    from app.core.projects.schema_inference import SchemaInferenceService, ConnectionValidator
    _schema_service = SchemaInferenceService(project_service)
    _validator = ConnectionValidator(_schema_service)


@router.post("", status_code=201)
@audit_action(action="project.create")
def create_project(
    data: ProjectCreateWithCanvas,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> dict[str, Any]:
    """Crea nuovo progetto. Se canvas è presente lo salva direttamente. Viewer non può creare."""
    metadata = _project_service.create_project(
        data,
        group_id=current_user.group_id,
        created_by=current_user.user_id,
    )

    canvas_warning = None
    if data.canvas:
        try:
            canvas_dict = data.canvas if isinstance(data.canvas, dict) else dict(data.canvas)
            canvas_dict = {k: v for k, v in canvas_dict.items() if not k.startswith("_")}
            _project_service.update_canvas(metadata.id, CanvasState(**canvas_dict))
        except (TypeError, ValueError) as e:
            canvas_warning = f"Canvas non salvato: schema non valido ({e})"
            logger.warning(
                f"create_project canvas save fallito per {metadata.id}: {e}",
                op="create_project"
            )
        except Exception as e:
            logger.error(
                f"[CRASH] create_project canvas save fallito per {metadata.id}: {e}",
                op="create_project", exc_info=True
            )
            try:
                _project_service.rollback_created_project(
                    metadata.id,
                    group_id=current_user.group_id,
                )
            except Exception as rollback_error:
                logger.error(
                    f"[CRASH] create_project rollback fallito per {metadata.id}: "
                    f"{rollback_error}",
                    op="create_project",
                    exc_info=True,
                )
            raise HTTPException(500, "Errore salvataggio canvas")

    canvas = _project_service.get_project_canvas(metadata.id)
    response = {
        "metadata": metadata.model_dump(),
        "canvas": canvas.model_dump() if canvas else None,
    }
    if canvas_warning:
        response["warning"] = canvas_warning
    return response


@router.get("")
def list_projects(
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Lista tutti i progetti con source_project_ids per manipola"""
    gid = group_filter(current_user)
    project_list = _project_service.list_projects(group_id=gid)

    enriched_projects = []
    for project in project_list.projects:
        project_dict = project.model_dump()

        if project.project_type == 'manipola':
            try:
                canvas = _project_service.get_project_canvas(project.id)
                if canvas and canvas.nodes:
                    source_ids = [
                        n.data.get('source_project_id') or n.source_project_id
                        for n in canvas.nodes
                        if n.type == 'project_source' and (n.data.get('source_project_id') or n.source_project_id)
                    ]
                    file_source_ids = [
                        n.file_source_id
                        for n in canvas.nodes
                        if n.type == 'file_source' and n.file_source_id
                    ]
                    project_dict['source_project_ids'] = source_ids
                    project_dict['file_source_ids'] = file_source_ids
                else:
                    project_dict['source_project_ids'] = []
                    project_dict['file_source_ids'] = []
            except CanvasCorruptedError as e:
                # Lista di progetti: NON rompiamo la pagina se un
                # singolo progetto ha canvas corrotto. Listiamo il
                # progetto con source_ids=[] ma marchiamo il campo
                # `canvas_corrupted=True` cosi' il frontend puo'
                # evidenziarlo (badge rosso) e l'admin sa quali
                # progetti richiedono ripristino
                logger.error(
                    f"Canvas corrotto in list_projects per {project.id}: "
                    f"{e.reason}",
                    op="list_projects",
                )
                project_dict['source_project_ids'] = []
                project_dict['file_source_ids'] = []
                project_dict['canvas_corrupted'] = True
                project_dict['canvas_corrupted_reason'] = e.reason
            except Exception:
                project_dict['source_project_ids'] = []
                project_dict['file_source_ids'] = []

        enriched_projects.append(project_dict)

    return {"projects": enriched_projects}


def _visible_project_ids_strict(
    project_ids: Any,
    owner_group_id: Optional[str],
    is_superadmin: bool,
) -> set[str]:
    """Return visible project ids using ownership-only project visibility.

    I progetti NON hanno tabella `project_shares` (a differenza di
    file_sources, connections, ecc.): la visibilita' coincide col gruppo
    proprietario. `visible_resource_ids("projects",...)` solleva
    `ValueError` perche' "projects" non e' nel whitelist `_SHAREABLE`,
    rompendo `update_project_canvas` e `_annotate_inaccessible_canvas_refs`
    con un 500 opaco.

    Questo helper sostituisce quella chiamata per il caso "validazione di
    una lista di project_id noti": ritorna il sottoinsieme di `project_ids`
    realmente visibile a `owner_group_id` (stesso group_id) o l'input
    integrale se `is_superadmin`.

    Uses one `WHERE id IN (...)` query instead of per-id lookups.
    """
    ids = {pid for pid in (project_ids or ()) if pid}
    if not ids:
        return set()
    if is_superadmin:
        return ids
    if not owner_group_id:
        return set()
    return list_owned_project_ids(ids, owner_group_id=owner_group_id)


def _metadata_group_id(metadata: Any) -> Optional[str]:
    """Return the group id from typed project metadata."""
    return getattr(metadata, "group_id", None)


def _annotate_inaccessible_canvas_refs(
    canvas_dict: dict[str, Any],
    owner_group_id: Optional[str],
    is_superadmin: bool,
) -> None:
    """Annotate serialized canvas nodes whose resources are not visible.

    Per ogni nodo `file_source` con `file_source_id` non visibile, oppure
    `project_source` con `source_project_id` non visibile, inietta nel
    sub-dict `data` del nodo:
      - `access_denied`: True
      - `access_denied_reason`: stringa breve operativa
      - `inaccessible_resource_type`: "file_source" | "project_source"

    Non rimuove nodi. Non muta il canvas persistito (riceve un dict gia'
    `model_dump`-ato). superadmin -> no-op (vede tutto).

    Args:
        canvas_dict: output di `CanvasState.model_dump` (mutato in-place
            sul campo `data` dei nodi affected).
        owner_group_id: gruppo proprietario del progetto (da `metadata`).
        is_superadmin: True se chi sta caricando e' superadmin.
    """
    if is_superadmin or canvas_dict is None or not owner_group_id:
        return
    nodes = canvas_dict.get("nodes") or []
    if not nodes:
        return

    file_source_ids: set = set()
    project_ids: set = set()
    arcgis_connection_ids: set = set()
    for n in nodes:
        ntype = n.get("type")
        ndata = n.get("data") or {}
        if ntype == "file_source":
            fsid = n.get("file_source_id") or ndata.get("file_source_id")
            if fsid:
                file_source_ids.add(fsid)
        elif ntype == "project_source":
            spid = n.get("source_project_id") or ndata.get("source_project_id")
            if spid:
                project_ids.add(spid)
        elif ntype == "carica_arcgis":
            agid = n.get("arcgis_connection_id") or ndata.get("arcgis_connection_id")
            if agid:
                arcgis_connection_ids.add(agid)
        elif ntype == "imdf_publisher":
            agid = (
                n.get("imdf_arcgis_connection_id")
                or ndata.get("imdf_arcgis_connection_id")
            )
            if agid:
                arcgis_connection_ids.add(agid)

    if not file_source_ids and not project_ids and not arcgis_connection_ids:
        return  # fast-path: zero DB hit

    from app.core.auth.visibility import visible_resource_ids
    visible_fs = (
        visible_resource_ids("file_sources", owner_group_id, is_superadmin=False)
        if file_source_ids
        else set()
    )
    visible_pr = _visible_project_ids_strict(
        project_ids,
        owner_group_id=owner_group_id,
        is_superadmin=False,
    )
    visible_ag = (
        visible_resource_ids("arcgis_connections", owner_group_id, is_superadmin=False)
        if arcgis_connection_ids
        else set()
    )

    # Mutate only the response dict; persisted canvases stay unchanged.
    for n in nodes:
        ntype = n.get("type")
        ndata = n.get("data")
        if ndata is None:
            ndata = {}
            n["data"] = ndata
        if ntype == "file_source":
            fsid = n.get("file_source_id") or ndata.get("file_source_id")
            if fsid and fsid not in visible_fs:
                ndata["access_denied"] = True
                ndata["access_denied_reason"] = (
                    f"File sorgente '{fsid}' non visibile al gruppo del progetto"
                )
                ndata["inaccessible_resource_type"] = "file_source"
        elif ntype == "project_source":
            spid = n.get("source_project_id") or ndata.get("source_project_id")
            if spid and spid not in visible_pr:
                ndata["access_denied"] = True
                ndata["access_denied_reason"] = (
                    f"Progetto sorgente '{spid}' non visibile al gruppo del progetto"
                )
                ndata["inaccessible_resource_type"] = "project_source"
        elif ntype == "carica_arcgis":
            agid = n.get("arcgis_connection_id") or ndata.get("arcgis_connection_id")
            if agid and agid not in visible_ag:
                ndata["access_denied"] = True
                ndata["access_denied_reason"] = (
                    f"Connessione ArcGIS '{agid}' non visibile al gruppo del progetto"
                )
                ndata["inaccessible_resource_type"] = "arcgis_connection"
        elif ntype == "imdf_publisher":
            agid = (
                n.get("imdf_arcgis_connection_id")
                or ndata.get("imdf_arcgis_connection_id")
            )
            if agid and agid not in visible_ag:
                ndata["access_denied"] = True
                ndata["access_denied_reason"] = (
                    f"Connessione ArcGIS '{agid}' non visibile al gruppo del progetto"
                )
                ndata["inaccessible_resource_type"] = "arcgis_connection"


@router.get("/{project_id}")
def get_project(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Ottieni progetto specifico (metadata + canvas + columns_metadata).

    Se il canvas in DB e' corrotto, `get_project_canvas` solleva
    `CanvasCorruptedError` e l'handler globale restituisce un errore
    esplicito invece di sostituirlo con un canvas vuoto.

    Canvas contenenti riferimenti cross-tenant
    a file_source / source_project (es. progetti duplicati o condivisi tra
    gruppi diversi) sono ritornati as-is dal DB,
    ma i nodi affected vengono marcati con `data.access_denied = True`.
    Il frontend usa il flag per disabilitare run/preview e mostrare un
    warning all'utente. Nessuna mutazione del canvas persistito.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    canvas = _project_service.get_project_canvas(project_id)
    columns_metadata = _project_service.get_project_columns_metadata(project_id)

    canvas_dict = canvas.model_dump() if canvas else None
    if canvas_dict is not None:
        _annotate_inaccessible_canvas_refs(
            canvas_dict,
            owner_group_id=_metadata_group_id(metadata),
            is_superadmin=(current_user.role == "superadmin"),
        )

    return {
        "metadata": metadata.model_dump(),
        "canvas": canvas_dict,
        "columns_metadata": columns_metadata
    }


@router.get("/{project_id}/canvas/raw")
def get_project_raw_canvas(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Read persisted canvas without CanvasState validation.

    Recovery-only endpoint for projects whose normal GET fails with
    CanvasCorruptedError. It remains read-only and keeps the same project
    group access gate as `get_project`.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    raw_canvas = _project_service.get_project_raw_canvas(
        project_id,
        group_id=None if current_user.role == "superadmin" else current_user.group_id,
    )
    if raw_canvas is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.PROJECT_NOT_FOUND,
                "message": f"Progetto '{project_id}' non trovato",
            },
        )

    return {
        "project_id": raw_canvas.project_id,
        "canvas": raw_canvas.canvas,
        "raw": True,
        "validated": False,
    }


@router.delete("/{project_id}", status_code=204)
@audit_action(action="project.delete", target_param="project_id")
def delete_project(
    project_id: str,
    current_user: CurrentUser = Depends(require_project_owner),
) -> None:
    """Elimina un progetto. Solo owner o admin/superadmin.

    Ritorna 409 Conflict se il progetto ha dipendenze che impediscono la
    cancellazione: eseguibili pubblicati a partire da esso, o altri progetti
    (manipola/arricchisci) che lo usano come sorgente.
    """
    try:
        _project_service.delete_project(project_id)
    except ProjectHasDependenciesError as e:
        # Shape 409 cascade canonica (6.1
        # { error, message, dependencies:[{kind, id, name, project_type?}] }
        # kind="eseguibile" per pubblicazioni bloccanti, kind="project"
        # per progetti downstream (manipola/arricchisci) che usano questo
        # come sorgente. Il FE usa handleCascade409 per rendere il toast
        deps: List[dict] = []
        for item in e.eseguibili:
            deps.append({
                "kind": "eseguibile",
                "id": item.get("id"),
                "name": item.get("name"),
            })
        for item in e.dependent_projects:
            entry = {
                "kind": "project",
                "id": item.get("id"),
                "name": item.get("name"),
            }
            if item.get("project_type") is not None:
                entry["project_type"] = item.get("project_type")
            deps.append(entry)
        raise HTTPException(
            status_code=409,
            detail={
                "error": error_codes.PROJECT_HAS_DEPENDENCIES,
                "message": str(e),
                "dependencies": deps,
            },
        )
    except ProjectCleanupPartialError as e:
        raise HTTPException(
            status_code=500,
            detail={
                "error": error_codes.PROJECT_CLEANUP_PARTIAL,
                "message": str(e),
                "project_id": e.project_id,
                "failed_steps": {
                    "critical": e.critical_failed,
                    "derived": e.derived_failed,
                },
                "project_preserved": True,
            },
        )


class CanvasWithPlugins(BaseModel):
    """Body del PUT /projects/{id}/canvas.

    `List[Node]` e `List[Edge]` fanno fallire il binding Pydantic con 422
    quando il payload non ha la struttura attesa.
    """
    nodes: List[Node] = []
    edges: List[Edge] = []
    plugins: Optional[dict] = None


def _collect_canvas_resource_refs(canvas: CanvasState) -> dict[str, set[str]]:
    """Estrae da `canvas` gli id di risorse cross-tenant referenziate dai
    nodi che le richiedono.

    Returns:
        dict con chiavi:
          - "file_sources": set[str] di `file_source_id` referenziati dai
            nodi `file_source`
          - "projects": set[str] di `source_project_id` referenziati dai
            nodi `project_source` (cerca sia `node.source_project_id` sia
            `node.data.get('source_project_id')`, allineato a `list_projects`
            che gia' supporta entrambi i path)
          - "arcgis_connections": set[str] di connection_id ArcGIS
            referenziati da nodi `carica_arcgis` (`arcgis_connection_id`)
            o `imdf_publisher` (`imdf_arcgis_connection_id`).

    Used by `update_project_canvas` for cross-tenant checks.
    """
    file_source_ids: set = set()
    project_ids: set = set()
    arcgis_connection_ids: set = set()
    for n in canvas.nodes:
        if n.type == 'file_source':
            fsid = getattr(n, 'file_source_id', None) or (
                n.data.get('file_source_id') if getattr(n, 'data', None) else None
            )
            if fsid:
                file_source_ids.add(fsid)
        elif n.type == 'project_source':
            spid = getattr(n, 'source_project_id', None) or (
                n.data.get('source_project_id') if getattr(n, 'data', None) else None
            )
            if spid:
                project_ids.add(spid)
        elif n.type == 'carica_arcgis':
            agid = getattr(n, 'arcgis_connection_id', None) or (
                n.data.get('arcgis_connection_id') if getattr(n, 'data', None) else None
            )
            if agid:
                arcgis_connection_ids.add(agid)
        elif n.type == 'imdf_publisher':
            agid = getattr(n, 'imdf_arcgis_connection_id', None) or (
                n.data.get('imdf_arcgis_connection_id') if getattr(n, 'data', None) else None
            )
            if agid:
                arcgis_connection_ids.add(agid)
    return {
        "file_sources": file_source_ids,
        "projects": project_ids,
        "arcgis_connections": arcgis_connection_ids,
    }


@router.put("/{project_id}/canvas")
@audit_action(action="project.update_canvas", target_param="project_id")
def update_project_canvas(
    project_id: str,
    body: CanvasWithPlugins,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> dict[str, str]:
    """Aggiorna canvas di un progetto. Qualsiasi user/admin/superadmin del
    gruppo proprietario (non solo l'owner): in un team i membri lavorano
    sui progetti dei colleghi. L'eliminazione del progetto resta invece
    riservata al solo owner (vedi DELETE /projects/{id})."""
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    canvas = CanvasState(nodes=body.nodes, edges=body.edges)

    # Reject orphan processing nodes before they become runtime failures.
    _SOURCE_LIKE = {
        "source", "feature_source", "project_source", "file_source",
        "staticValues", "datasource_node", "comment",
    }
    target_ids = {e.target for e in canvas.edges}
    orphans = [
        n.id for n in canvas.nodes
        if n.type not in _SOURCE_LIKE and n.id not in target_ids
    ]
    if orphans:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Canvas contiene nodi orfani (nessun edge incoming): "
                f"{', '.join(orphans)}. Aggiungi almeno una relazione "
                f"verso ciascuno di questi nodi, oppure rimuovili dal "
                f"canvas prima di salvare."
            ),
        )

    # Save-time cross-tenant validation gives a clear error before runtime.
    refs = _collect_canvas_resource_refs(canvas)
    if refs["file_sources"] or refs["projects"] or refs["arcgis_connections"]:
        from app.core.auth.visibility import visible_resource_ids
        owner_group_id = _metadata_group_id(metadata)
        is_super = current_user.role == "superadmin"
        invalid_file_sources: set = set()
        invalid_projects: set = set()
        invalid_arcgis_connections: set = set()
        if refs["file_sources"]:
            visible_fs = visible_resource_ids(
                "file_sources", owner_group_id, is_superadmin=is_super,
            )
            invalid_file_sources = refs["file_sources"] - visible_fs
        if refs["projects"]:
            visible_pr = _visible_project_ids_strict(
                refs["projects"],
                owner_group_id=owner_group_id,
                is_superadmin=is_super,
            )
            invalid_projects = refs["projects"] - visible_pr
        if refs["arcgis_connections"]:
            visible_ag = visible_resource_ids(
                "arcgis_connections", owner_group_id, is_superadmin=is_super,
            )
            invalid_arcgis_connections = refs["arcgis_connections"] - visible_ag
        if invalid_file_sources or invalid_projects or invalid_arcgis_connections:
            parts = []
            if invalid_file_sources:
                parts.append(
                    f"file_source: {', '.join(sorted(invalid_file_sources))}"
                )
            if invalid_projects:
                parts.append(
                    f"project_source: {', '.join(sorted(invalid_projects))}"
                )
            if invalid_arcgis_connections:
                parts.append(
                    f"arcgis_connection: {', '.join(sorted(invalid_arcgis_connections))}"
                )
            logger.warning(
                f"[SECURITY] update_project_canvas blocked cross-tenant refs "
                f"on {project_id} (owner_group={owner_group_id}): "
                f"{'; '.join(parts)}",
                op="update_canvas",
            )
            raise HTTPException(
                status_code=400,
                detail=(
                    "Il canvas contiene riferimenti a risorse non visibili "
                    "al gruppo proprietario del progetto: "
                    f"{'; '.join(parts)}. Rimuovi o sostituisci i nodi "
                    "interessati prima di salvare."
                ),
            )

    # Backend recomputes result columns before persisting to avoid stale
    # frontend-provided canvas columns.
    try:
        from app.shared.execution.canvas_columns import (
            compute_result_columns,
            find_result_node,
        )
        result_node = find_result_node(canvas)
        if result_node is not None:
            from app.core.projects.schema_inference import SchemaInferenceService

            computed = compute_result_columns(
                canvas=canvas,
                result_id=result_node.id,
                project_id=project_id,
                project_service=_project_service,
                schema_inference_service_factory=SchemaInferenceService,
            )
            if computed:
                from app.core.projects.canvas_models import Column
                result_node.columns = [Column(**c) for c in computed]
    except Exception as _ccerr:
        # Column recompute is diagnostic; malformed canvases continue through
        # the normal validation/persistence path.
        logger.warning(
            f"C9 compute_result_columns fallito (non bloccante): {_ccerr}",
            exc_info=True,
        )

    try:
        if not _project_service.update_canvas(project_id, canvas):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{project_id}' non trovato",
                },
            )
    except HTTPException:
        raise
    except ProjectLineageCycleError as e:
        raise HTTPException(400, str(e)) from e

    columns_metadata = []
    try:
        def _extract_cols(columns: Any) -> list[dict[str, str]]:
            out = []
            for col in (columns or []):
                if isinstance(col, dict):
                    if col.get('visible') == False:
                        continue
                    col_name = col.get('alias') or col.get('name', '')
                    col_type = col.get('type', 'str')
                    if col_name:
                        out.append({"name": col_name, "type": col_type})
                elif hasattr(col, 'name'):
                    if hasattr(col, 'visible') and col.visible == False:
                        continue
                    col_name = getattr(col, 'alias', None) or col.name
                    col_type = getattr(col, 'type', 'str')
                    if col_name:
                        out.append({"name": col_name, "type": col_type})
                elif isinstance(col, str):
                    out.append({"name": col, "type": "str"})
            return out

        result_node = next((n for n in canvas.nodes if n.type == 'result'), None)

        if not result_node:
            for n in reversed(canvas.nodes):
                if n.type in ('transform', 'table', 'source') and (n.columns or (n.data and n.data.get('columns'))):
                    result_node = n
                    break

        if result_node:
            columns = result_node.columns or (result_node.data.get('columns', []) if result_node.data else [])
            columns_metadata = _extract_cols(columns)

        # Per canvas ARRICCHISCI: ricava colonne da sorgenti + output_mapping api_enrich
        if not columns_metadata:
            seen = set()
            for n in canvas.nodes:
                node_data = n.data or {}
                node_type = n.type

                if node_type in ('project_source', 'file_source', 'staticValues'):
                    src_cols = n.columns or node_data.get('columns', [])
                    for c in _extract_cols(src_cols):
                        if c['name'] not in seen:
                            columns_metadata.append(c)
                            seen.add(c['name'])

                if node_type == 'api_enrich':
                    for mapping in (node_data.get('output_mapping') or []):
                        col_name = mapping.get('column') or mapping.get('target_column') or mapping.get('name', '')
                        col_type = mapping.get('type', 'str')
                        if col_name and col_name not in seen:
                            columns_metadata.append({"name": col_name, "type": col_type})
                            seen.add(col_name)

        if columns_metadata:
            _project_service.update_project_columns_metadata(project_id, columns_metadata)

    except Exception as e:
        logger.warning(f"Errore estrazione columns_metadata: {e}")

    return {"status": "ok", "project_id": project_id}


class ValidationRequest(BaseModel):
    """Request per validazione connessione"""
    source_node_id: str
    target_node_id: str
    config: Optional[dict] = None


@router.get("/{project_id}/schemas")
def get_all_schemas(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Ottiene lo schema (colonne + tipi) per tutti i nodi del progetto."""
    if not _schema_service:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="Schema service non inizializzato",
        )
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        schemas = _schema_service.infer_all_schemas(project_id)
        return {"schemas": schemas}
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{project_id}/schemas/{node_id}")
def get_node_schema(
    project_id: str,
    node_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Ottiene lo schema per un singolo nodo."""
    if not _schema_service:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="Schema service non inizializzato",
        )
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        columns = _schema_service.infer_node_schema(project_id, node_id)
        return {"node_id": node_id, "columns": columns}
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/{project_id}/validate-connection")
def validate_connection(
    project_id: str,
    request: ValidationRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Valida una connessione tra due nodi."""
    if not _validator:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="Validator non inizializzato",
        )
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        is_valid, error = _validator.validate_connection(
            project_id=project_id,
            source_node_id=request.source_node_id,
            target_node_id=request.target_node_id,
            connection_config=request.config
        )
        return {"valid": is_valid, "error": error}
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{project_id}/validate-all")
def validate_all_connections(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> dict[str, Any]:
    """Valida tutte le connessioni del progetto."""
    if not _validator:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="Validator non inizializzato",
        )
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        errors = _validator.validate_all_connections(project_id)
        return {"valid": len(errors) == 0, "errors": errors}
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/admin/cleanup-orphans")
@audit_action(action="admin.cleanup_orphans")
def admin_cleanup_orphans(
    dry_run: bool = True,
    current_user: CurrentUser = Depends(require_superadmin),
) -> dict[str, Any]:
    """Cleanup degli orfani cross-DB (results.db / intermediate.db).

    Trova e cancella righe in `results.db` e `intermediate.db` che fanno
    riferimento a `project_id` non più esistenti in `config.projects`. Sono
    creati quando un progetto viene cancellato senza passare per
    `delete_project` (cancellazione DB diretta, restore parziale, crash, ecc.).

    Solo superadmin. Per default è dry-run (audit) — passare `?dry_run=false`
    per eseguire effettivamente il cleanup.

    Returns: report dettagliato con conteggi e lista project_id rimossi.
    """
    from app.core.results.orphan_cleanup import cleanup_orphans
    logger.info(
        f"admin_cleanup_orphans avviato da {current_user.user_id} "
        f"(dry_run={dry_run})",
        op="admin_cleanup_orphans"
    )
    return cleanup_orphans(dry_run=dry_run)
