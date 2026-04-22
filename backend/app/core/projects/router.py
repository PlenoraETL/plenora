"""
Router API per il modulo Projects.

Endpoint CRUD progetti, gestione canvas, e inferenza schema.
Estratti da main.py e api/schema.py per rendere il modulo autocontenuto.

Endpoints:
- POST   /api/projects                              → crea progetto
- GET    /api/projects                               → lista progetti
- GET    /api/projects/{id}                          → dettaglio progetto
- DELETE /api/projects/{id}                          → elimina progetto
- PUT    /api/projects/{id}/canvas                   → aggiorna canvas
- GET    /api/projects/{id}/schemas                  → schema tutti i nodi
- GET    /api/projects/{id}/schemas/{node_id}        → schema singolo nodo
- POST   /api/projects/{id}/validate-connection      → valida connessione nodi
- GET    /api/projects/{id}/validate-all             → valida tutte le connessioni
"""

from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Any, List, Optional

from app.utils.logger import get_logger
from app.shared.audit_action import audit_action
from app.shared.models import CanvasState, Node, Edge
from app.shared.exceptions import (
    ProjectHasDependenciesError,
    CanvasCorruptedError,
    ProjectCleanupPartialError,
)
from app.services.project_lineage import ProjectLineageCycleError
from app.core.auth.auth import (
    get_current_user,
    require_not_esecutore,
    require_user_or_above,
    require_project_owner,
    require_superadmin,
    assert_group_access,
    CurrentUser,
    group_filter,
)
from .models import ProjectCreate

class ProjectCreateWithCanvas(ProjectCreate):
    canvas: Any = None
    plugins: Any = None         # ignorato — rimasto per compatibilità frontend


logger = get_logger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

# Servizi inizializzati da main.py
_project_service = None
_schema_service = None
_validator = None


def init_projects(project_service):
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _project_service, _schema_service, _validator
    _project_service = project_service
    # Inizializza anche schema inference
    from app.services.schema_inference import SchemaInferenceService, ConnectionValidator
    _schema_service = SchemaInferenceService(project_service)
    _validator = ConnectionValidator(_schema_service)


# ==================== CRUD PROGETTI ====================

@router.post("")
@audit_action(action="project.create")
def create_project(data: ProjectCreateWithCanvas, current_user: CurrentUser = Depends(require_user_or_above)):
    """Crea nuovo progetto. Se canvas è presente lo salva direttamente. Viewer non può creare."""
    metadata = _project_service.create_project(
        data,
        group_id=current_user.group_id,
        created_by=current_user.user_id,
    )

    # Se canvas fornito, salvalo direttamente. Errori specifici di parsing/
    # validazione sono catturati e segnalati nel response come warning, ma
    # non rollbackano la creazione del progetto (per backward compat).
    canvas_warning = None
    if data.canvas:
        try:
            canvas_dict = data.canvas if isinstance(data.canvas, dict) else dict(data.canvas)
            # Rimuovi campi interni prefissati con _
            canvas_dict = {k: v for k, v in canvas_dict.items() if not k.startswith("_")}
            _project_service.update_canvas(metadata.id, CanvasState(**canvas_dict))
        except (TypeError, ValueError) as e:
            canvas_warning = f"Canvas non salvato: schema non valido ({e})"
            logger.warning(
                f"create_project canvas save fallito per {metadata.id}: {e}",
                op="create_project"
            )
        except Exception as e:
            # Errore inatteso: rilancia 500 invece di nascondere il problema.
            logger.error(
                f"[CRASH] create_project canvas save fallito per {metadata.id}: {e}",
                op="create_project", exc_info=True
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
def list_projects(current_user: CurrentUser = Depends(require_not_esecutore)):
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
                # progetti richiedono ripristino.
                logger.error(
                    f"Canvas corrotto in list_projects per {project.id}: "
                    f"{e.reason}",
                    op="list_projects",
                )
                project_dict['source_project_ids'] = []
                project_dict['file_source_ids'] = []
                project_dict['canvas_corrupted'] = True
            except Exception:
                project_dict['source_project_ids'] = []
                project_dict['file_source_ids'] = []

        enriched_projects.append(project_dict)

    return {"projects": enriched_projects}


@router.get("/{project_id}")
def get_project(project_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni progetto specifico (metadata + canvas + columns_metadata).

    Se il canvas in DB e' corrotto, `get_project_canvas` solleva
    `CanvasCorruptedError` → handler globale (main.py) restituisce 500
    con detail esplicito + error_code=canvas_corrupted. Pre-fix il
    fallback silenzioso a canvas vuoto portava l'utente a sovrascrivere
    i dati corrotti originali senza accorgersene.
    """
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    canvas = _project_service.get_project_canvas(project_id)
    columns_metadata = _project_service.get_project_columns_metadata(project_id)

    return {
        "metadata": metadata.model_dump(),
        "canvas": canvas.model_dump() if canvas else None,
        "columns_metadata": columns_metadata
    }


@router.delete("/{project_id}")
@audit_action(action="project.delete", target_param="project_id")
def delete_project(project_id: str, current_user: CurrentUser = Depends(require_project_owner)):
    """Elimina un progetto. Solo owner o admin/superadmin.

    Ritorna 409 Conflict se il progetto ha dipendenze che impediscono la
    cancellazione: eseguibili pubblicati a partire da esso, o altri progetti
    (manipola/arricchisci) che lo usano come sorgente.
    """
    try:
        _project_service.delete_project(project_id)
    except ProjectHasDependenciesError as e:
        # Shape 409 cascade canonica (Batch Fix 6.1):
        # { error, message, dependencies:[{kind, id, name, project_type?}] }.
        # kind="eseguibile" per pubblicazioni bloccanti, kind="project"
        # per progetti downstream (manipola/arricchisci) che usano questo
        # come sorgente. Il FE usa handleCascade409 per rendere il toast.
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
                "error": "project_has_dependencies",
                "message": str(e),
                "dependencies": deps,
            },
        )
    except ProjectCleanupPartialError as e:
        # Shape 500 cleanup-partial (Batch 9 C6):
        # { error, message, project_id, failed_steps:{critical,derived},
        #   project_preserved:true }.
        # Fail-closed: la row config.projects e' preservata (vedi C5).
        # `project_preserved:true` telegrama l'invariante al caller che puo'
        # ritentare o escalare senza rischiare di lasciare il sistema in
        # stato incoerente.
        raise HTTPException(
            status_code=500,
            detail={
                "error": "project_cleanup_partial",
                "message": str(e),
                "project_id": e.project_id,
                "failed_steps": {
                    "critical": e.critical_failed,
                    "derived": e.derived_failed,
                },
                "project_preserved": True,
            },
        )
    return {"status": "deleted", "project_id": project_id}


class CanvasWithPlugins(BaseModel):
    """Body del PUT /projects/{id}/canvas.

    C11: pre-fix `nodes: Any, edges: Any` accettava qualsiasi struttura
    (anche stringa, int, None) e la validazione strutturale veniva
    delegata a CanvasState più avanti — che a sua volta poteva produrre
    errori oscuri a runtime. Ora tipizziamo con `List[Node]/List[Edge]`:
    Pydantic rifiuta 422 al binding se il payload non matcha, con
    messaggi diagnostici per ogni campo errato.
    """
    nodes: List[Node] = []
    edges: List[Edge] = []
    plugins: Optional[dict] = None   # ignorato — rimasto per compatibilità frontend


@router.put("/{project_id}/canvas")
@audit_action(action="project.update_canvas", target_param="project_id")
def update_project_canvas(
    project_id: str,
    body: CanvasWithPlugins,
    current_user: CurrentUser = Depends(require_user_or_above),
):
    """Aggiorna canvas di un progetto. Qualsiasi user/admin/superadmin del
    gruppo proprietario (non solo l'owner): in un team i membri lavorano
    sui progetti dei colleghi. L'eliminazione del progetto resta invece
    riservata al solo owner (vedi DELETE /projects/{id})."""
    # Group check: solo membri del gruppo proprietario possono modificare il canvas
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")

    canvas = CanvasState(nodes=body.nodes, edges=body.edges)

    # C11: reject canvas con nodi orfani. Un nodo di tipo non-source
    # (es. transform, api_enrich, result, carica, multi_join) senza
    # alcun edge incoming provoca a runtime:
    # - JOIN: CROSS JOIN silente (ora refused da query_generator) →
    #   errore in esecuzione;
    # - RESULT: nessuna fonte dati → output vuoto o errore;
    # - TRANSFORM: input schema None → crash executor.
    # Molto meglio rifiutare al save con messaggio claro.
    # Esenti: nodi che sono sorgenti per definizione (source,
    # feature_source, project_source, file_source, staticValues,
    # datasource_node) + comment (non influenza il flusso dati).
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

    # C9: backend single source of truth per result_node.columns.
    # Pre-fix, il frontend poteva inviare columns stale/vuote (vedi
    # canvasSerializer.js:270 che ricade su `saved` quando l'upstream
    # diretto non ha data.columns — tipico per api_enrich). Post-fix,
    # il backend ricomputa autorevolmente via SchemaInferenceService
    # (che gia' gestisce tutti i node type + BFS via edges) e inietta
    # nel canvas in-memory PRIMA di persistere. Cosi' il canvas
    # salvato ha result.columns corrette, il reload UI legge il
    # canvas e il ResultNode mostra le colonne giuste.
    try:
        from app.shared.canvas_columns import (
            compute_result_columns,
            find_result_node,
        )
        result_node = find_result_node(canvas)
        if result_node is not None:
            computed = compute_result_columns(
                canvas=canvas,
                result_id=result_node.id,
                project_id=project_id,
                project_service=_project_service,
            )
            if computed:
                # Mutate in-place: il canvas verra' persistito subito
                # dopo con update_canvas(). Column(BaseModel) accetta
                # **dict via pydantic.
                from app.shared.models import Column
                result_node.columns = [Column(**c) for c in computed]
    except Exception as _ccerr:
        # Non bloccare il save se il compute fallisce (canvas malformato,
        # node type sconosciuto, ecc.). Cade nel comportamento
        # pre-fix (frontend columns preserved). Loggato per visibility.
        logger.warning(
            f"C9 compute_result_columns fallito (non bloccante): {_ccerr}",
            exc_info=True,
        )

    try:
        if not _project_service.update_canvas(project_id, canvas):
            raise HTTPException(404, f"Progetto '{project_id}' non trovato")
    except HTTPException:
        raise
    except ProjectLineageCycleError as e:
        # Batch Fix 6.4: narrow catch tipizzato (era isinstance inside broad).
        # La chain `from e` preserva il traceback originale nel log backend
        # senza leakarlo nel detail HTTP (FastAPI serializza solo HTTPException).
        raise HTTPException(400, str(e)) from e

    # Estrai e salva columns_metadata dal nodo Result
    columns_metadata = []
    try:
        def _extract_cols(columns):
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


# ==================== SCHEMA INFERENCE ====================

class ValidationRequest(BaseModel):
    """Request per validazione connessione"""
    source_node_id: str
    target_node_id: str
    config: Optional[dict] = None


@router.get("/{project_id}/schemas")
def get_all_schemas(project_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottiene lo schema (colonne + tipi) per tutti i nodi del progetto."""
    if not _schema_service:
        raise HTTPException(500, "Schema service non inizializzato")
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        schemas = _schema_service.infer_all_schemas(project_id)
        return {"schemas": schemas}
    except Exception as e:
        raise HTTPException(500, "Errore interno del server")


@router.get("/{project_id}/schemas/{node_id}")
def get_node_schema(project_id: str, node_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottiene lo schema per un singolo nodo."""
    if not _schema_service:
        raise HTTPException(500, "Schema service non inizializzato")
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        columns = _schema_service.infer_node_schema(project_id, node_id)
        return {"node_id": node_id, "columns": columns}
    except Exception as e:
        raise HTTPException(500, "Errore interno del server")


@router.post("/{project_id}/validate-connection")
def validate_connection(project_id: str, request: ValidationRequest, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Valida una connessione tra due nodi."""
    if not _validator:
        raise HTTPException(500, "Validator non inizializzato")
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
    except Exception as e:
        raise HTTPException(500, "Errore interno del server")


@router.get("/{project_id}/validate-all")
def validate_all_connections(project_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Valida tutte le connessioni del progetto."""
    if not _validator:
        raise HTTPException(500, "Validator non inizializzato")
    metadata = _project_service.get_project_metadata(project_id)
    assert_group_access(metadata, current_user, f"Progetto '{project_id}'")
    try:
        errors = _validator.validate_all_connections(project_id)
        return {"valid": len(errors) == 0, "errors": errors}
    except Exception as e:
        raise HTTPException(500, "Errore interno del server")


# ==================== ADMIN: ORPHAN CLEANUP ====================

@router.post("/admin/cleanup-orphans")
@audit_action(action="admin.cleanup_orphans")
def admin_cleanup_orphans(
    dry_run: bool = True,
    current_user: CurrentUser = Depends(require_superadmin),
):
    """Cleanup degli orfani cross-DB (results.db / intermediate.db).

    Trova e cancella righe in `results.db` e `intermediate.db` che fanno
    riferimento a `project_id` non più esistenti in `config.projects`. Sono
    creati quando un progetto viene cancellato senza passare per
    `delete_project` (cancellazione DB diretta, restore parziale, crash, ecc.).

    Solo superadmin. Per default è dry-run (audit) — passare `?dry_run=false`
    per eseguire effettivamente il cleanup.

    Returns: report dettagliato con conteggi e lista project_id rimossi.
    """
    from app.services.orphan_cleanup_service import cleanup_orphans
    logger.info(
        f"admin_cleanup_orphans avviato da {current_user.user_id} "
        f"(dry_run={dry_run})",
        op="admin_cleanup_orphans"
    )
    return cleanup_orphans(dry_run=dry_run)
