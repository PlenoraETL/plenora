"""
Router API per il modulo Projects.

Endpoint CRUD progetti, gestione canvas, e inferenza schema.
"""
from app.core.eseguibili.identity_transform import preview_transform
from app.core.eseguibili.manifest import compile_manifest
from app.core.manipola.canvas_data_preparation import CanvasDataPreparation, PREPARATION_ERRORS, classify_preparation_error
from app.core.orchestrator.execution_contract import PipelineExecutionContract
from app.core.orchestrator.runtime_inputs import apply_identity_inputs, apply_project_runtime_inputs
from app.shared.execution.runtime_identity import RuntimeIdentity, RuntimeIdentityError, identity_input_nodes
from app.shared.execution.runtime_bound_fields import strip_runtime_bound_fields
from app.core.orchestrator.carica_payload_validation import assert_canvas_rest_auth_usable
from app.core.projects.canvas_validation import _StaticCanvasService
from app.core.projects.canvas_validation import validate_project_chain
from app.core.projects.query_preview import describe_estrai_query
from app.core.projects import schema_execution_sources
from app.shared.execution.lookup_mapping import lookup_value_text

from fastapi import APIRouter, Body, HTTPException, Depends, Query
from pydantic import BaseModel, Field, field_validator
from typing import Annotated, Any, List, Optional

from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.execution.canvas_column import Column
from app.observability.audit_action import audit_action
from psycopg import Error as PsycopgError
from app.core.projects.canvas_models import CanvasState, Node, Edge
from app.core.projects.canvas_revision import CanvasRevisionConflict, canvas_version
from app.core.projects.exceptions import (
    CanvasCorruptedError,
    ProjectCleanupPartialError,
    ProjectHasActiveTasksError,
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
from app.core.projects.usage import (
    list_owned_project_ids,
    list_manipola_source_refs,
    list_projects_with_canvas_for_datasource_check,
)
from app.core.auth import visibility as auth_visibility
from app.core import output_contract as output_contract_module
from app.core.projects import canvas_access as _canvas_access
from app.core.projects import schema_inference as schema_inference_module
from app.core.projects import source_freshness as source_freshness_module
from app.core.results import orphan_cleanup as orphan_cleanup_module
from app.core.projects.types import (
    CanvasResourceRefs,
    JsonObject,
)
from app.shared.execution import canvas_columns as canvas_columns_module
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
    _schema_service = schema_inference_module.SchemaInferenceService(project_service)
    _validator = schema_inference_module.ConnectionValidator(_schema_service)


@router.post("", status_code=201)
@audit_action(action="project.create")
def create_project(
    data: ProjectCreateWithCanvas,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """Crea nuovo progetto. Se canvas è presente lo salva direttamente. Viewer non può creare."""
    parsed_canvas: CanvasState | None = None
    if data.canvas:
        try:
            canvas_dict = data.canvas if isinstance(data.canvas, dict) else dict(data.canvas)
            canvas_dict = {k: v for k, v in canvas_dict.items() if not k.startswith("_")}
            parsed_canvas = CanvasState(**canvas_dict)
        except (TypeError, ValueError) as e:
            logger.warning(
                f"create_project canvas validation fallita prima della creazione: {e}",
                op="create_project",
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": str(e)[:500],
                },
            )

    if parsed_canvas is not None or data.default_connection_id:
        _assert_canvas_refs_visible_for_owner(
            parsed_canvas or CanvasState(),
            owner_group_id=current_user.group_id,
            is_superadmin=current_user.role == "superadmin",
            default_connection_id=data.default_connection_id,
        )

    metadata = _project_service.create_project(
        data,
        group_id=current_user.group_id,
        created_by=current_user.user_id,
    )

    canvas_warning = None
    if data.canvas:
        try:
            _project_service.update_canvas(metadata.id, parsed_canvas or CanvasState())
        except (TypeError, ValueError) as e:
            # Canvas invalido e' 400: il chiamante deve vedere il dettaglio
            # Pydantic invece di ricevere success con canvas vuoto.
            canvas_warning = f"Canvas non salvato: schema non valido ({e})"
            logger.warning(
                f"create_project canvas save fallito per {metadata.id}: {e}",
                op="create_project",
            )
            # Cleanup progetto orfano (canvas dichiarato ma non persistibile)
            try:
                _project_service.rollback_created_project(
                    metadata.id, group_id=current_user.group_id,
                )
            except Exception as _rb:
                logger.warning(f"rollback fallito: {_rb}", op="create_project")
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "validation_error",
                    "message": str(e)[:500],
                },
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


class CloneProjectRequest(BaseModel):
    """Richiesta clone progetto. `name` opzionale → default 'Copia di …'."""
    name: Optional[str] = None


@router.post("/{project_id}/clone", status_code=201)
@audit_action(action="project.clone", target_param="project_id")
def clone_project(
    project_id: str,
    data: CloneProjectRequest,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """Clona un progetto esistente (canvas + impostazioni) in uno nuovo.

    Solo progetti visibili al proprio gruppo (assert_group_access). Il nuovo
    progetto eredita type/connessione/descrizione, copia il canvas as-is e
    l'output_contract (con producer_project_id riscritto); canvas_validated è
    resettato dal salvataggio canvas. Viewer non può clonare (require_user_or_above).
    """
    source = _project_service.get_project_metadata(project_id)
    assert_group_access(source, current_user, f"Progetto '{project_id}'")

    new_name = ((data.name or "").strip() or f"Copia di {source.name}")[:255]

    try:
        cloned = _project_service.clone_project(
            source_project_id=project_id,
            new_name=new_name,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
    except ProjectLineageCycleError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(e)[:500]},
        )

    if cloned is None:
        raise_typed_http(
            error_codes.PROJECT_NOT_FOUND, status_code=404,
            message="Progetto sorgente non trovato",
        )

    canvas = _project_service.get_project_canvas(cloned.id)
    return {
        "metadata": cloned.model_dump(),
        "canvas": canvas.model_dump() if canvas else None,
    }


class ProjectDetailsUpdate(BaseModel):
    """Aggiornamento metadati progetto (rename / descrizione / tag / titolo
    della catena della home: stringa vuota = titolo calcolato)."""
    name: Optional[str] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    chain_title: Optional[str] = None


@router.patch("/{project_id}")
@audit_action(action="project.update", target_param="project_id")
def update_project_details(
    project_id: str,
    data: ProjectDetailsUpdate,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """Rinomina (e/o aggiorna la descrizione di) un progetto.

    Solo metadati: non tocca canvas né validazione. Group-access richiesto;
    viewer escluso (require_user_or_above). Nome non vuoto, cap 255.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    name = data.name.strip() if data.name is not None else None
    if name is not None:
        if not name:
            raise HTTPException(
                status_code=400,
                detail={"error": "validation_error", "message": "Il nome non può essere vuoto"},
            )
        name = name[:255]

    updated = _project_service.update_project_details(
        project_id, name=name, description=data.description, tags=data.tags,
        chain_title=data.chain_title,
    )
    return {"metadata": updated.model_dump()}


class CreateVersionRequest(BaseModel):
    """Snapshot canvas: etichetta opzionale."""
    label: Optional[str] = None


@router.post("/{project_id}/versions", status_code=201)
@audit_action(action="project.version_create", target_param="project_id")
def create_canvas_version(
    project_id: str,
    data: CreateVersionRequest,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """Salva uno snapshot del canvas corrente (#12). Mantiene le ultime 30."""
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    version = _project_service.create_canvas_version(
        project_id, label=data.label,
        group_id=current_user.group_id, created_by=current_user.user_id,
    )
    if version is None:
        raise_typed_http(error_codes.PROJECT_NOT_FOUND, status_code=404, message="Progetto non trovato")
    return version.model_dump()


@router.get("/{project_id}/versions")
def list_canvas_versions(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Elenco delle versioni salvate del canvas (metadati, senza il blob)."""
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    return {"versions": [v.model_dump() for v in _project_service.list_canvas_versions(project_id)]}


class CanvasRestoreRequest(BaseModel):
    # Token opaco ``canvas_version`` ricevuto da GET/PUT/ripristino, rimandato
    # tale e quale (F3). Assente = scrittura compatibile senza controllo.
    base_canvas_version: str | None = None


@router.post("/{project_id}/versions/{version_id}/restore")
@audit_action(action="project.version_restore", target_param="project_id")
def restore_canvas_version(
    project_id: str,
    version_id: str,
    current_user: CurrentUser = Depends(require_user_or_above),
    body: CanvasRestoreRequest | None = None,
) -> JsonObject:
    """Ripristina il canvas del progetto a una versione salvata."""
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        revision = {}
        base = body.base_canvas_version if body else None
        canvas = _project_service.restore_canvas_version(project_id, version_id, revision_out=revision, **({"base_canvas_version": base} if base is not None else {}))
    except CanvasRevisionConflict as exc:
        raise HTTPException(409, detail=exc.to_dict()) from exc
    except ProjectLineageCycleError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "message": str(e)[:500]},
        )
    if canvas is None:
        raise_typed_http(error_codes.RUN_NOT_FOUND, status_code=404, message="Versione non trovata")
    updated = _project_service.get_project_metadata(project_id)
    return {"canvas": canvas.model_dump(), "canvas_version": revision.get("canvas_version"),
            "updated_at": revision.get("updated_at") or (getattr(updated, "updated_at", None) if updated else None)}


@router.get("")
def list_projects(
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Lista tutti i progetti con source_project_ids per manipola"""
    gid = group_filter(current_user)
    project_list = _project_service.list_projects(
        group_id=gid, limit=limit, offset=offset,
    )

    # UI-1 (C24): le sorgenti dei progetti Manipola le estrae il DB (una
    # query sugli id dei nodi, nessun canvas verso l'applicazione). Se la
    # query mirata non e' disponibile (es. PostgreSQL < 16 senza
    # pg_input_is_valid) si torna al percorso di prima: canvas in batch.
    manipola_ids = {str(p.id) for p in project_list.projects if p.project_type == 'manipola'}
    refs_by_id = None
    try:
        refs_by_id = {r.project_id: r for r in list_manipola_source_refs(manipola_ids)}
    except (PsycopgError, RuntimeError) as exc:
        # Errore del DB (funzione assente, DB non configurato): ripiego.
        logger.warning(
            f"list_projects: query mirata delle sorgenti non riuscita, "
            f"si leggono i canvas: {type(exc).__name__}: {exc}",
            op="list_projects",
        )

    # Percorso di riserva: batch-fetch dei canvas manipola in 1 query invece
    # di N+1 chiamate a `get_project_canvas`.
    # NB: salta `sync_canvas_with_output_contracts` (side-effect del
    # `get_project_canvas` full) — il list endpoint non ne ha bisogno
    # e il sync e' gia' fatto in fase di save/load del canvas detail.
    canvas_by_id: dict = {}
    if refs_by_id is None:
        try:
            for row in list_projects_with_canvas_for_datasource_check(manipola_ids):
                if row.get("project_type") == "manipola":
                    canvas_by_id[str(row["id"])] = row.get("canvas")
        except Exception as exc:
            # Fallback graceful al pattern N+1 se la batch fallisce.
            logger.warning(
                f"list_projects: batch canvas load fallita, fallback "
                f"N+1: {type(exc).__name__}: {exc}",
                op="list_projects",
            )

    enriched_projects = []
    for project in project_list.projects:
        project_dict = project.model_dump()

        if project.project_type == 'manipola' and refs_by_id is not None:
            refs = refs_by_id.get(str(project.id))
            project_dict['source_project_ids'] = list(refs.source_project_ids) if refs else []
            project_dict['file_source_ids'] = list(refs.file_source_ids) if refs else []
            if refs and refs.corrupted:
                logger.error(
                    f"Canvas corrotto in list_projects per {project.id}: JSON non valido",
                    op="list_projects",
                )
                project_dict['canvas_corrupted'] = True
                project_dict['canvas_corrupted_reason'] = "JSON malformato"
        elif project.project_type == 'manipola':
            try:
                # Fast path: usa il canvas pre-caricato (dict raw).
                # Slow path (fallback batch fallita): get_project_canvas
                # per quel singolo progetto.
                raw_canvas = canvas_by_id.get(project.id)
                if raw_canvas is None and not canvas_by_id:
                    canvas = _project_service.get_project_canvas(project.id)
                    raw_canvas = (
                        canvas.model_dump() if canvas is not None else None
                    )
                if raw_canvas and raw_canvas.get('nodes'):
                    nodes = raw_canvas.get('nodes') or []
                    source_ids = [
                        (n.get('data') or {}).get('source_project_id')
                        or n.get('source_project_id')
                        for n in nodes
                        if n.get('type') == 'project_source'
                        and (
                            (n.get('data') or {}).get('source_project_id')
                            or n.get('source_project_id')
                        )
                    ]
                    file_source_ids = [
                        n.get('file_source_id')
                        for n in nodes
                        if n.get('type') == 'file_source'
                        and n.get('file_source_id')
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
    """Return visible project ids using ownership-only project visibility."""
    return _canvas_access.visible_project_ids_strict(
        project_ids,
        owner_group_id=owner_group_id,
        is_superadmin=is_superadmin,
        list_owned_project_ids_fn=lambda ids, owner_group_id: list_owned_project_ids(
            ids,
            owner_group_id=owner_group_id,
        ),
    )


def _metadata_group_id(metadata: Any) -> Optional[str]:
    """Return the group id from typed project metadata."""
    return _canvas_access.metadata_group_id(metadata)


def _annotate_inaccessible_canvas_refs(
    canvas_dict: dict[str, Any],
    owner_group_id: Optional[str],
    is_superadmin: bool,
) -> None:
    return _canvas_access.annotate_inaccessible_canvas_refs(
        canvas_dict,
        owner_group_id=owner_group_id,
        is_superadmin=is_superadmin,
        visible_resource_ids_fn=auth_visibility.visible_resource_ids,
        visible_project_ids_fn=_visible_project_ids_strict,
    )


@router.get("/{project_id}")
def get_project(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Ottieni progetto specifico (metadata + canvas + output_contract).

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
    output_contract = _project_service.get_project_output_contract(project_id)

    # #2 (trasparenza rename): raccoglie i rename di colonna auto-applicati ai
    # project_source (rinominati a monte) per notificarli al FE invece di
    # riscriverli in silenzio.
    applied_migrations: list = []
    if canvas is not None:
        try:
            output_contract_module.sync_canvas_with_output_contracts(
                canvas=canvas,
                project_id=project_id,
                project_service=_project_service,
                applied_migrations=applied_migrations,
                schema_inference_factory=schema_inference_module.SchemaInferenceService,
            )
        except Exception as exc:
            logger.warning(
                f"sync output_contract canvas fallito per {project_id}: {exc}",
                exc_info=True,
            )
        try:
            source_freshness_module.annotate_canvas_project_source_freshness(
                canvas,
                expected_group_id=_metadata_group_id(metadata),
            )
        except Exception as exc:
            logger.warning(
                f"annotazione freshness project_source fallita per {project_id}: {exc}",
                exc_info=True,
            )

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
        "output_contract": output_contract,
        "applied_migrations": applied_migrations,
        # Token della versione del canvas persistito (base per PUT/ripristino, F3).
        "canvas_version": _canvas_version_of(project_id) if canvas is not None else None,
    }


def _canvas_version_of(project_id: str) -> str | None:
    """Token ``canvas_version`` del canvas persistito, se leggibile."""
    raw = _project_service.get_project_raw_canvas(project_id)
    canvas = getattr(raw, "canvas", None) if raw is not None else None
    return canvas_version(canvas) if isinstance(canvas, (dict, str, list)) else None


@router.get("/{project_id}/canvas/raw")
def get_project_raw_canvas(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
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
    except ProjectHasActiveTasksError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": error_codes.PROJECT_HAS_ACTIVE_TASKS,
                "message": str(e),
                "project_id": e.project_id,
                "task_ids": e.task_ids,
            },
        )
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
    # Solo PUT /canvas: token ``canvas_version`` della versione di base.
    base_canvas_version: str | None = None


def _collect_canvas_resource_refs(canvas: CanvasState) -> CanvasResourceRefs:
    """Extract cross-tenant resource ids referenced by a canvas."""
    return _canvas_access.collect_canvas_resource_refs(canvas)


def _assert_canvas_refs_visible_for_owner(
    canvas: CanvasState,
    *,
    owner_group_id: str,
    is_superadmin: bool,
    default_connection_id: str | None = None,
) -> None:
    """Fail closed when a project canvas references resources from another group."""
    refs = _collect_canvas_resource_refs(canvas)
    if default_connection_id:
        refs.setdefault("connections", set()).add(default_connection_id)
    if not any(refs.values()):
        return

    invalid_refs = _canvas_access.find_inaccessible_canvas_refs(
        refs,
        owner_group_id=owner_group_id,
        is_superadmin=is_superadmin,
        visible_resource_ids_fn=auth_visibility.visible_resource_ids,
        visible_project_ids_fn=_visible_project_ids_strict,
    )
    parts = _canvas_access.format_inaccessible_canvas_refs(invalid_refs)
    if not parts:
        return

    logger.warning(
        f"[SECURITY] blocked cross-tenant canvas refs "
        f"(owner_group={owner_group_id}): {'; '.join(parts)}",
        op="validate_canvas_refs",
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


def _prepare_canvas_for_owner(
    project_id: str, canvas: CanvasState, metadata: Any, current_user: CurrentUser,
    *, schema_call: Any = None,
) -> CanvasState:
    """Prepara e verifica il canvas senza persistere modifiche al progetto.

    Stessa preparazione per il salvataggio (PUT /canvas) e per gli schemi di
    un canvas non salvato (POST /schemas): controllo dei riferimenti tra
    gruppi e sincronizzazione con i contratti. Un passo senza ingresso NON
    blocca il salvataggio (collaudo live L5, coerente con L3): Valida lo
    segnala (errore se arriva all'Output, avviso se e' in un ramo che non ci
    arriva) e la conferma di eliminazione del frontend lo dice prima.

    ``schema_call`` (UI-2, C15: SchemaInferenceService.begin_call): le
    colonne dell'Output si calcolano con la chiamata di inferenza che la
    richiesta riusera' subito dopo (POST /schemas); i nodi che la
    preparazione modifica (l'Output e quelli a valle) escono dalle sue cache.
    """
    # I campi scritti solo dalla risoluzione degli input runtime non arrivano
    # mai dal client (shared.execution.runtime_bound_fields).
    strip_runtime_bound_fields(canvas)
    # Verifica i riferimenti prima di leggere gli schemi dei progetti sorgente.
    _assert_canvas_refs_visible_for_owner(
        canvas,
        owner_group_id=_metadata_group_id(metadata) or "",
        is_superadmin=current_user.role == "superadmin",
    )

    try:
        output_contract_module.sync_canvas_with_output_contracts(
            canvas=canvas,
            project_id=project_id,
            project_service=_project_service,
            schema_inference_factory=schema_inference_module.SchemaInferenceService,
        )
    except Exception as exc:
        logger.warning(
            f"sync output_contract canvas fallito per {project_id}: {exc}",
            exc_info=True,
        )

    # Ricalcola le colonne senza modificare il contratto del risultato materializzato.
    result_node = None
    try:
        result_node = canvas_columns_module.find_result_node(canvas)
        if result_node is not None:
            computed = canvas_columns_module.compute_result_columns(
                canvas=canvas,
                result_id=result_node.id,
                project_id=project_id,
                project_service=_project_service,
                schema_inference_service_factory=(
                    (lambda _service: schema_call) if schema_call is not None
                    else schema_inference_module.SchemaInferenceService
                ),
            )
            if computed:
                result_node.columns = [Column(**c) for c in computed]
        if schema_call is not None and result_node is not None:
            # L'Output ha colonne nuove: lui e i nodi a valle si ricalcolano.
            schema_call.forget_downstream(canvas, {result_node.id})
    except Exception as _ccerr:
        if schema_call is not None:
            schema_call.forget_downstream(canvas, {n.id for n in canvas.nodes})
        # Il ricalcolo diagnostico non blocca la normale validazione del canvas.
        logger.warning(
            f"C9 compute_result_columns fallito (non bloccante): {_ccerr}",
            exc_info=True,
        )

    try:
        source_freshness_module.clear_canvas_project_source_freshness(canvas)
    except Exception as exc:
        logger.warning(
            f"clear freshness project_source fallito per {project_id}: {exc}",
            exc_info=True,
        )

    return canvas


@router.put("/{project_id}/canvas")
@audit_action(action="project.update_canvas", target_param="project_id")
def update_project_canvas(
    project_id: str,
    body: CanvasWithPlugins,
    current_user: CurrentUser = Depends(require_user_or_above),
) -> JsonObject:
    """Aggiorna canvas di un progetto. Qualsiasi user/admin/superadmin del
    gruppo proprietario (non solo l'owner): in un team i membri lavorano
    sui progetti dei colleghi. L'eliminazione del progetto resta invece
    riservata al solo owner (vedi DELETE /projects/{id})."""
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    canvas = CanvasState(nodes=body.nodes, edges=body.edges)

    canvas = _prepare_canvas_for_owner(project_id, canvas, metadata, current_user)
    # Chi salva un riferimento a un connettore API deve poterlo usare e
    # l'endpoint REST deve avere la sua origine (carica.rest_auth).
    assert_canvas_rest_auth_usable(
        [node.model_dump() for node in canvas.nodes],
        current_user,
        project_group_id=_metadata_group_id(metadata) or "",
    )

    try:
        revision = {}
        if not _project_service.update_canvas(project_id, canvas, revision_out=revision, **({"base_canvas_version": body.base_canvas_version} if body.base_canvas_version is not None else {})):
            raise HTTPException(
                status_code=404,
                detail={
                    "error": error_codes.PROJECT_NOT_FOUND,
                    "message": f"Progetto '{project_id}' non trovato",
                },
            )
    except CanvasRevisionConflict as exc:
        raise HTTPException(409, detail=exc.to_dict()) from exc
    except HTTPException:
        raise
    except ProjectLineageCycleError as e:
        raise HTTPException(400, str(e)) from e

    updated = _project_service.get_project_metadata(project_id)
    return {"status": "ok", "project_id": project_id, "canvas_version": revision.get("canvas_version"),
            "updated_at": revision.get("updated_at") or (getattr(updated, "updated_at", None) if updated else None)}


class ValidationRequest(BaseModel):
    """Request per validazione connessione"""
    source_node_id: str
    target_node_id: str
    config: Optional[dict] = None


class SchemaInferenceRequest(BaseModel):
    canvas: CanvasWithPlugins

    @field_validator("canvas")
    @classmethod
    def validate_canvas_size(cls, canvas: CanvasWithPlugins) -> CanvasWithPlugins:
        """Limita il lavoro sincrono richiesto dall'inferenza non persistita."""
        if len(canvas.nodes) > 500 or len(canvas.edges) > 2000:
            raise ValueError("Il canvas supera il limite di 500 nodi o 2000 collegamenti")
        return canvas


class SchemaColumnResponse(BaseModel):
    """Colonna prevista, con identità e presenza indipendenti dal tipo."""
    id: str
    name: str
    type: str
    source_name: str
    source_node: str | None = None
    is_calculated: bool = False
    optional: bool = False


class SchemaStateResponse(BaseModel):
    names_status: str
    types_status: str
    order_status: str
    reason: str = ""


class SchemasResponse(BaseModel):
    schemas: dict[str, list[SchemaColumnResponse]]
    schema_states: dict[str, SchemaStateResponse]


@router.post("/{project_id}/schemas", response_model=SchemasResponse)
def infer_project_schemas(
    project_id: str,
    body: SchemaInferenceRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Inferisce gli schemi del canvas non salvato senza modificare il progetto."""
    canvas = CanvasState(nodes=body.canvas.nodes, edges=body.canvas.edges)
    return _infer_project_schemas(project_id, current_user, canvas=canvas)


@router.get("/{project_id}/schemas", response_model=SchemasResponse)
def get_all_schemas(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Ottiene lo schema (colonne + tipi) per tutti i nodi del progetto."""
    return _infer_project_schemas(project_id, current_user)


def _infer_project_schemas(
    project_id: str,
    current_user: CurrentUser,
    canvas: CanvasState | None = None,
) -> JsonObject:
    if not _schema_service:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="Schema service non inizializzato",
        )
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    # UI-2 (C15): una sola chiamata di inferenza per la preparazione e la
    # mappa (prima: il grafo si inferiva da capo due volte per richiesta).
    shared = canvas is not None and isinstance(_schema_service, schema_inference_module.SchemaInferenceService)
    call = _schema_service.begin_call() if shared else None
    if canvas is not None:
        canvas = _prepare_canvas_for_owner(project_id, canvas, metadata, current_user, schema_call=call)
    try:
        if call is not None:
            return _schema_service.infer_schemas_response(project_id, canvas=canvas, call=call)
        return _schema_service.infer_schemas_response(project_id, canvas=canvas)
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/{project_id}/query-preview")
def preview_project_query(
    project_id: str,
    body: SchemaInferenceRequest,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
    """Query di ESTRAI che l'esecuzione lancerebbe sul canvas non salvato.

    Fase 5, T6 (R3.13): stesso piano dell'esecuzione (estrai/query_plan), mai
    una ricostruzione nel frontend. Non modifica il progetto.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    if getattr(metadata, "project_type", None) != "estrai":
        raise HTTPException(400, "Anteprima della query disponibile solo per i progetti Estrai")
    canvas = _prepare_canvas_for_owner(
        project_id, CanvasState(nodes=body.canvas.nodes, edges=body.canvas.edges),
        metadata, current_user,
    )
    connection_id = getattr(metadata, "default_connection_id", None)
    return describe_estrai_query(
        canvas, project_id=project_id, project_type="estrai",
        default_connection_id=connection_id if isinstance(connection_id, str) else None,
        load_sql_options=schema_execution_sources.load_sql_options,
    )


class DistinctRequest(BaseModel):
    canvas: CanvasWithPlugins
    column: str | None = None
    column_id: str | None = None
    limit: int = 100

    @field_validator("limit")
    @classmethod
    def bounded_limit(cls, value: int) -> int:
        if not 1 <= value <= 1000:
            raise ValueError("limit deve essere compreso fra 1 e 1000")
        return value


def with_live_api_details(canvas: Any, api_configs: dict) -> Any:
    """Canvas con i dettagli ATTUALI delle connessioni API negli api_enrich.

    Fase 5, T6 (R2.9, R2.10, N11): il nodo porta il riferimento (``api_id``);
    parametri e parametri statici si leggono dalla connessione. Le copie
    salvate nei canvas vecchi restano solo il ripiego quando la connessione
    non e' leggibile. Solo per la validazione: il canvas salvato non cambia.
    """
    if not api_configs or canvas is None:
        return canvas
    dumped = canvas.model_dump()
    changed = False
    for node in dumped.get("nodes") or []:
        if node.get("type") != "api_enrich":
            continue
        data = node.get("data") if isinstance(node.get("data"), dict) else {}
        config = api_configs.get(node.get("api_id") or data.get("api_id"))
        if not config:
            continue
        for key in ("parameters", "static_parameters"):
            if key not in config:
                continue
            data[key] = config[key]
            if key in node:
                node[key] = config[key]
            changed = True
        node["data"] = data
    return CanvasState(**dumped) if changed else canvas


def raise_preparation_failure(error: BaseException) -> None:
    """Unico mappatore HTTP degli errori della preparazione in memoria
    (distinct, preflight Arricchisci e validate/estimate per nodo): 4xx con
    ``details.reason`` esplicito, mai 500 per le famiglie note."""
    failure = classify_preparation_error(error)
    logger.warning("[GESTITO] Preparazione dati fallita (%s): %s", failure.reason, type(error).__name__)
    raise_typed_http(error_codes.VALIDATION_ERROR, status_code=failure.status_code, message=failure.message,
                     details={"reason": failure.reason})


@router.post("/{project_id}/nodes/{node_id}/distinct")
def distinct_node_values(project_id: str, node_id: str, body: DistinctRequest,
                         current_user: CurrentUser = Depends(require_not_esecutore)) -> JsonObject:
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    canvas = _prepare_canvas_for_owner(project_id, CanvasState(nodes=body.canvas.nodes, edges=body.canvas.edges), metadata, current_user)
    # Anteprima dei valori: «Utente che esegue» = username di prova (runtime_identity).
    try:
        if identity_input_nodes(canvas):
            canvas = CanvasState(**apply_identity_inputs(PipelineExecutionContract(
                project_id=project_id, project_type=metadata.project_type, group_id=metadata.group_id,
                canvas=canvas.model_dump(), runtime_context={"default_connection_id": getattr(metadata, "default_connection_id", None)}),
                RuntimeIdentity.preview_of(canvas)).canvas)
    except RuntimeIdentityError as exc:
        raise_typed_http(exc.code, status_code=400, message=exc.message)
    except ValueError as exc:
        raise_typed_http(error_codes.VALIDATION_ERROR, status_code=400, message=str(exc))
    preparation = CanvasDataPreparation(canvas, metadata.group_id)
    if node_id not in preparation.nodes:
        raise HTTPException(404, "Nodo non trovato")
    try:
        source_ids = preparation.input_ids(node_id)
        name = body.column
        if body.column_id and source_ids:
            service = schema_inference_module.SchemaInferenceService(_StaticCanvasService(canvas))
            columns = service.infer_node_schema_typed(project_id, source_ids[0]).columns
            name = next((col["name"] for col in columns if col.get("id") == body.column_id), None)
            if name is None:
                raise HTTPException(422, "column_id non presente nell'ingresso del nodo")
        frame, sampled, reason = preparation.input(node_id)
    except PREPARATION_ERRORS as exc:
        raise_preparation_failure(exc)
    if frame is None:
        return {"values": [], "truncated": False, "sampled": False, "sampled_rows": 0, "reason": reason}
    if not name or name not in frame.columns:
        raise HTTPException(422, f"Colonna '{name}' assente nell'ingresso del nodo")
    if list(frame.columns).count(name) != 1:
        raise HTTPException(422, f"Colonna '{name}' non univoca nell'ingresso del nodo")
    counts = {}
    truncated = False
    for value in frame[name]:
        # Stessa forma di testo dell'esecuzione del Lookup (lookup_mapping.py):
        # il null resta null (value None, is_null True), mai "None"/"nan".
        text_value = lookup_value_text(value)
        key = (text_value is None, text_value)
        if key in counts:
            counts[key] += 1
        elif len(counts) < body.limit:
            counts[key] = 1
        else:
            truncated = True
    return {"values": [{"value": value, "is_null": null, "count": count} for (null, value), count in counts.items()],
            "column": name, "column_id": body.column_id, "truncated": truncated or sampled,
            "sampled": sampled, "sampled_rows": len(frame), "reason": None}


@router.get("/{project_id}/schemas/{node_id}")
def get_node_schema(
    project_id: str,
    node_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
) -> JsonObject:
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
) -> JsonObject:
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
) -> JsonObject:
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


class IdentityTransformPreviewRequest(BaseModel):
    """Anteprima della «Trasformazione» dell'input «Utente che esegue»."""
    expression: str = Field(max_length=500)
    username: str = Field(min_length=1, max_length=200)


class IdentityTransformPreviewResponse(BaseModel):
    success: bool
    value: Optional[str] = None
    message: Optional[str] = None


@router.post("/identity-transform-preview", response_model=IdentityTransformPreviewResponse)
def preview_identity_transform(
    body: IdentityTransformPreviewRequest,
    # Solo chi puo' modificare i progetti (D2): il viewer non scrive canvas.
    current_user: CurrentUser = Depends(require_user_or_above),
) -> IdentityTransformPreviewResponse:
    """Valore della trasformazione per il nome utente di PROVA del pannello
    (es. «E0041 → 004_IS»). Stessa regola dell'esecuzione
    (``eseguibili.identity_transform``): stesso cancello, stesso motore, stessi
    messaggi. Nessun dato letto: valuta solo l'espressione sul testo inviato;
    non da' alcun valore a un'esecuzione (quello lo calcola il server
    dall'utente che esegue)."""
    try:
        value = preview_transform(body.expression, body.username.strip())
    except RuntimeIdentityError as exc:
        return IdentityTransformPreviewResponse(success=False, message=exc.message)
    except ValueError as exc:
        return IdentityTransformPreviewResponse(success=False, message=str(exc))
    return IdentityTransformPreviewResponse(success=True, value=value)


class CanvasValidationRequest(BaseModel):
    canvas: CanvasWithPlugins | None = None
    inputs: dict | None = None


@router.post("/{project_id}/validate")
def validate_project_canvas(
    project_id: str,
    current_user: CurrentUser = Depends(require_not_esecutore),
    body: CanvasValidationRequest | None = None,
) -> JsonObject:
    """Valida l'intero canvas del progetto.

    Orizzontale: strutturale + per-nodo (i ``validate_node`` registrati al
    composition-root). Ritorna il ``ValidationReport`` tipizzato. Non blocca nulla
    (il gate a esecuzione e' separato); e' la valida-on-demand del pulsante.
    Persiste lo stato (``canvas_validated``) con l'esito: True se la catena valida
    senza errori, altrimenti False.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:

        selected_canvas = (CanvasState(nodes=body.canvas.nodes, edges=body.canvas.edges)
                           if body and body.canvas else _project_service.get_project_canvas(project_id))
        if body and body.canvas:
            selected_canvas = _prepare_canvas_for_owner(project_id, selected_canvas, metadata, current_user)
        manifest = None
        input_error = None
        input_error_code = "runtime_inputs.invalid"
        try:
            manifest = compile_manifest(for_project=True, source_project_id=project_id, project_type=metadata.project_type,
                                        canvas=selected_canvas.model_dump(), default_connection_id=getattr(metadata, "default_connection_id", None) if isinstance(getattr(metadata, "default_connection_id", None), str) else None)
            if body and body.inputs is not None:
                contract = apply_project_runtime_inputs(PipelineExecutionContract(
                    project_id=project_id, project_type=metadata.project_type, group_id=metadata.group_id,
                    canvas=selected_canvas.model_dump(), runtime_context={"default_connection_id": getattr(metadata, "default_connection_id", None)}), body.inputs,
                    # Valida con input = anteprima: username di prova.
                    identity=RuntimeIdentity.preview_of(selected_canvas))
                selected_canvas = CanvasState(**contract.canvas)
        except RuntimeIdentityError as exc:
            input_error, input_error_code = exc.message, exc.code
        except ValueError as exc:
            input_error = str(exc)

        api_configs: dict = {}

        def _load(pid: str):
            # I ref project_source sono già autorizzati al PUT
            # (find_inaccessible_canvas_refs); qui risolviamo canvas+tipo.
            md = _project_service.get_project_metadata(pid)
            if not md:
                return None
            loaded_canvas = selected_canvas if pid == project_id else _project_service.get_project_canvas(pid)
            return (with_live_api_details(loaded_canvas, api_configs), getattr(md, "project_type", None))

        # ARRICCHISCI_API_CONNECTION_STALE è DB-dependent: l'endpoint (boundary
        # DB-aware) fornisce gli id delle connessioni API esistenti; il gate
        # orizzontale non lo fa. Usiamo TUTTE le connessioni (no filtro gruppo) per
        # non flaggare falsamente connessioni condivise/cross-group (#A mai falso
        # blocco): STALE scatta solo se l'api_id non esiste affatto.
        try:
            # Via il SERVICE arricchisci (boundary di dominio), non il leaf .db:
            # niente import cross-domain di app.core.<domain>.db nel router projects.
            # group_id=None = tutte le connessioni (no falso STALE su cross-group).
            from app.core.arricchisci.api_connector import api_connection_service
            listed = [c for c in api_connection_service.list_api_connections(group_id=None) if c.get("id")]
            available_api_ids = {c["id"] for c in listed}
            # Fase 5, T6 (R2.10): parametri della connessione ATTUALE, non
            # la copia salvata nel nodo (che puo' essere vecchia).
            api_configs.update({c["id"]: c for c in listed})
        except Exception:
            available_api_ids = None  # degrada: salta STALE, non rompe la validazione

        report = validate_project_chain(
            project_id, _load, available_api_connection_ids=available_api_ids,
            load_execution_context=lambda pid: {"default_connection_id": (
                contract.runtime_context.get("default_connection_id")
                if pid == project_id and body and body.inputs is not None and input_error is None
                else getattr(_project_service.get_project_metadata(pid), "default_connection_id", None))},
        )
        result = report.to_dict()
        if input_error:
            result["issues"].append({"node_id": "", "severity": "error", "code": input_error_code, "message": input_error})
        result["valid"] = not report.has_errors and input_error is None
        result["has_errors"] = not result["valid"]
        result["manifest"] = manifest.model_dump() if manifest else None
        result["checks"] = {"configuration": "verified" if result["valid"] else "invalid",
                            "runtime_inputs": "verified" if body and body.inputs is not None and not input_error else "not_provided",
                            "data": "not_checked", "external_dependencies": "not_guaranteed",
                            "resources": "not_guaranteed"}
        if not body or body.canvas is None:
            _project_service.set_canvas_validated(project_id, result["valid"])
        return result
    except Exception:
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


class OrphanCleanupScope(BaseModel):
    """Perimetro confermato dall'operatore: solo questi project_id orfani."""
    project_ids: List[str] = Field(..., max_length=5000)


@router.post("/admin/cleanup-orphans")
@audit_action(action="admin.cleanup_orphans")
def admin_cleanup_orphans(
    dry_run: bool = True,
    scope: Optional[OrphanCleanupScope] = Body(None),
    current_user: CurrentUser = Depends(require_superadmin),
) -> JsonObject:
    """Cleanup degli orfani cross-DB (results / intermediate storage).

    Trova e cancella record result/intermediate che fanno
    riferimento a `project_id` non più esistenti in `config.projects`. Sono
    creati quando un progetto viene cancellato senza passare per
    `delete_project` (cancellazione DB diretta, restore parziale, crash, ecc.).

    Solo superadmin. Per default è dry-run (audit) — passare `?dry_run=false`
    per eseguire effettivamente il cleanup.

    Con un corpo `{"project_ids": [...]}` la bonifica tocca SOLO quei
    progetti, e solo se sono davvero orfani: il perimetro della richiesta e'
    quello che l'operatore ha visto e confermato (audit frontend R10.4).

    Returns: report dettagliato con conteggi e lista project_id rimossi.
    """
    logger.info(
        f"admin_cleanup_orphans avviato da {current_user.user_id} "
        f"(dry_run={dry_run})",
        op="admin_cleanup_orphans"
    )
    return orphan_cleanup_module.cleanup_orphans(
        dry_run=dry_run,
        project_ids=scope.project_ids if scope is not None else None,
    )
