"""Helper centralizzati per i check di autorizzazione sulle risorse
cross-cutting (`project_id`, `file_source_id`) usati dai router HTTP.

Single point of truth: ogni endpoint che accetta un id di risorsa
dall'utente passa per uno di questi helper PRIMA di leggere i dati,
cosi' un utente autenticato non puo' esfiltrare risorse di altri
tenant indovinandone l'id.

  - `assert_project_access`           → owned only (write ops)
  - `assert_project_visible`          → owned + shared (read ops)
  - `assert_file_source_access`       → owned + shared (file_sources)
  - `assert_canvas_resources_access`  → scan completo del canvas
"""

from fastapi import HTTPException

from app.core.auth.models import CurrentUser
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
)
from app.services.project_service import ProjectService
import app.db.db_service as db_svc


def assert_project_access(project_id: str, current_user: CurrentUser) -> dict:
    """Verifica che il progetto esista e appartenga al gruppo dell'utente.

    Raises 400 se `project_id` vuoto, 404 se inesistente, 403 se di
    un altro gruppo (superadmin bypassa). Ritorna il metadata su success.
    Da usare per operazioni di scrittura — per la lettura su risorse
    condivise vedi `assert_project_visible`.
    """
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id mancante")
    meta = ProjectService().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
    assert_group_access(meta, current_user, f"Progetto '{project_id}'")
    return meta


def assert_project_visible(project_id: str, current_user: CurrentUser) -> dict:
    """Variante "soft" per endpoint di sola lettura: accetta progetti
    owned + condivisi via sharing. Per operazioni di scrittura usa
    `assert_project_access`."""
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id mancante")
    meta = ProjectService().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
    assert_resource_visible(meta, "projects", current_user, f"Progetto '{project_id}'")
    return meta


def assert_file_source_access(file_source_id: str, current_user: CurrentUser) -> dict:
    """Verifica che il file_source esista e sia visibile all'utente
    (owned + shared via allowed_groups + superadmin bypass).

    Raises 400 se vuoto, 404 se inesistente, 403 se non visibile.
    Ritorna il row del file_source su success.
    """
    if not file_source_id:
        raise HTTPException(status_code=400, detail="file_source_id mancante")
    row = db_svc.get_file_source(file_source_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"File source '{file_source_id}' non trovato")
    assert_resource_visible(row, "file_sources", current_user, f"File source '{file_source_id}'")
    return row


def assert_canvas_resources_access(canvas: dict, current_user: CurrentUser) -> None:
    """Scansiona i nodi del canvas e verifica l'accesso a tutte le
    risorse referenziate (project_source, file_source, extra_files
    inline degli api_enrich). Raises 403/404 al primo nodo non
    autorizzato.

    Va chiamato dal router HTTP PRIMA di creare il task celery: senza
    questo scan un utente con accesso al proprio progetto puo'
    costruire un canvas che referenzia file_source/project_source di
    altri tenant, e il task li caricherebbe a runtime senza contesto
    HTTP utente per validarli.
    """
    if not canvas or not isinstance(canvas, dict):
        return
    nodes = canvas.get("nodes") or []
    if not isinstance(nodes, list):
        return

    def _get(n, key):
        # I nodi possono avere campi sia flat sia dentro `data`.
        if not isinstance(n, dict):
            return None
        return n.get(key) or (n.get("data") or {}).get(key)

    for node in nodes:
        ntype = node.get("type") if isinstance(node, dict) else None

        if ntype == "file_source":
            fsid = _get(node, "file_source_id")
            if fsid:
                assert_file_source_access(fsid, current_user)

        elif ntype == "project_source":
            spid = _get(node, "source_project_id")
            if spid:
                assert_project_visible(spid, current_user)

        elif ntype == "api_enrich":
            # extra_files inline: {field: {file_source_id, ...}}
            extra = _get(node, "extra_files") or {}
            if isinstance(extra, dict):
                for v in extra.values():
                    if isinstance(v, dict):
                        fid = v.get("file_source_id")
                    else:
                        fid = v
                    if fid:
                        assert_file_source_access(fid, current_user)
