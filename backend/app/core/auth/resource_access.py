"""
Helper centralizzati per i check di autorizzazione sulle risorse

cross-cutting (`project_id`, `file_source_id`) usati dai router HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

from app.shared.contracts.auth import CurrentUser
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
)
from app.core.auth.ports import FileSourceReader, ProjectMetadataReader
from app.core.files.ownership import FileSourceAccess
from app.core.projects.access_context import ProjectMetadataAccess
from app.core.projects.models import ProjectMetadata
from app.core.projects.upstream_identity import assert_saved_result_available as _assert_saved_result_available

if TYPE_CHECKING:
    from app.core.files.db import FileSourcePublicRow


# I reader sono iniettabili dal composition root (`bind_resource_readers`);
# il fallback lazy costruisce gli adapter dei domini proprietari per worker
# e contesti senza bootstrap esplicito.
_project_metadata_reader: Optional[ProjectMetadataReader] = None
_file_source_reader: Optional[FileSourceReader] = None


def bind_resource_readers(
    *,
    project_metadata_reader: Optional[ProjectMetadataReader] = None,
    file_source_reader: Optional[FileSourceReader] = None,
) -> None:
    """Inietta i reader (composition root o fake nei test). Idempotente;
    passare None lascia invariato il binding corrente."""
    global _project_metadata_reader, _file_source_reader
    if project_metadata_reader is not None:
        _project_metadata_reader = project_metadata_reader
    if file_source_reader is not None:
        _file_source_reader = file_source_reader


def reset_resource_readers() -> None:
    """Torna al fallback lazy (per i test che hanno iniettato fake)."""
    global _project_metadata_reader, _file_source_reader
    _project_metadata_reader = None
    _file_source_reader = None


def _require_project_metadata_reader() -> ProjectMetadataReader:
    global _project_metadata_reader
    if _project_metadata_reader is None:
        _project_metadata_reader = ProjectMetadataAccess()
    return _project_metadata_reader


def _require_file_source_reader() -> FileSourceReader:
    global _file_source_reader
    if _file_source_reader is None:
        _file_source_reader = FileSourceAccess()
    return _file_source_reader


def assert_project_access(
    project_id: str,
    current_user: CurrentUser,
) -> ProjectMetadata:
    """Verifica che il progetto esista e appartenga al gruppo dell'utente.

    Raises 400 se `project_id` vuoto, 404 se inesistente, 403 se di
    un altro gruppo (superadmin bypassa). Ritorna il metadata su success.
    Da usare per operazioni di scrittura — per la lettura su risorse
    condivise vedi `assert_project_visible`.
    """
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id mancante")
    meta = _require_project_metadata_reader().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
    assert_group_access(meta, current_user, f"Progetto '{project_id}'")
    return meta


def assert_saved_result_available(project_id: str, current_user: CurrentUser) -> None:
    """Porta U6 del risultato SALVATO (``projects.upstream_identity``):
    409 se il progetto filtra per utente, salvo il superadmin. Qui per i
    domini che passano gia' dal confine d'accesso (Pianificazioni, Feature
    Service)."""
    _assert_saved_result_available(project_id, current_user)


def assert_project_visible(
    project_id: str,
    current_user: CurrentUser,
) -> ProjectMetadata:
    """Variante "soft" per endpoint di sola lettura: accetta progetti
    owned + condivisi via sharing. Per operazioni di scrittura usa
    `assert_project_access`."""
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id mancante")
    meta = _require_project_metadata_reader().get_project_metadata(project_id)
    if not meta:
        raise HTTPException(status_code=404, detail=f"Progetto '{project_id}' non trovato")
    assert_resource_visible(meta, "projects", current_user, f"Progetto '{project_id}'")
    return meta


def assert_file_source_access(
    file_source_id: str,
    current_user: CurrentUser,
) -> FileSourcePublicRow:
    """Verifica che il file_source esista e sia visibile all'utente
    (owned + shared via allowed_groups + superadmin bypass).

    Raises 400 se vuoto, 404 se inesistente, 403 se non visibile.
    Ritorna il row del file_source su success.
    """
    if not file_source_id:
        raise HTTPException(status_code=400, detail="file_source_id mancante")
    row = _require_file_source_reader().get_file_source(file_source_id)
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
        # I nodi possono avere campi sia flat sia dentro `data`
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
