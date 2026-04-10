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
from typing import Any, Optional

from app.utils.logger import get_logger
from app.shared.models import CanvasState
from app.shared.exceptions import ProjectHasDependenciesError
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
            except Exception:
                project_dict['source_project_ids'] = []
                project_dict['file_source_ids'] = []

        enriched_projects.append(project_dict)

    return {"projects": enriched_projects}


@router.get("/{project_id}")
def get_project(project_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Ottieni progetto specifico (metadata + canvas + columns_metadata)"""
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
def delete_project(project_id: str, current_user: CurrentUser = Depends(require_project_owner)):
    """Elimina un progetto. Solo owner o admin/superadmin.

    Ritorna 409 Conflict se il progetto ha dipendenze che impediscono la
    cancellazione: eseguibili pubblicati a partire da esso, o altri progetti
    (manipola/arricchisci) che lo usano come sorgente.
    """
    try:
        _project_service.delete_project(project_id)
    except ProjectHasDependenciesError as e:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "project_has_dependencies",
                "message": str(e),
                "eseguibili": e.eseguibili,
                "dependent_projects": e.dependent_projects,
            },
        )
    return {"status": "deleted", "project_id": project_id}


class CanvasWithPlugins(BaseModel):
    nodes: Any = []
    edges: Any = []
    plugins: Optional[dict] = None   # ignorato — rimasto per compatibilità frontend


@router.put("/{project_id}/canvas")
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

    if not _project_service.update_canvas(project_id, canvas):
        raise HTTPException(404, f"Progetto '{project_id}' non trovato")

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
