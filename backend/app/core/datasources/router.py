"""
PLENORA-ETL — Router Data Source

Endpoints CRUD per la gestione dei Data Source e operazioni sui file.

Endpoints configurazione:
    POST   /api/datasources                      → crea Data Source
    GET    /api/datasources                      → lista Data Source
    GET    /api/datasources/{id}                 → dettaglio
    PUT    /api/datasources/{id}                 → aggiorna
    DELETE /api/datasources/{id}                 → elimina
    POST   /api/datasources/{id}/test            → testa connessione

Endpoints operazioni file:
    GET    /api/datasources/{id}/files           → list_files
    GET    /api/datasources/{id}/files/download  → leggi file raw
    POST   /api/datasources/{id}/files/upload    → scrivi file (multipart)
"""

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Depends
from fastapi.responses import Response
from typing import Optional, List, Dict

from app.utils.logger import get_logger
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.shared.audit_action import audit_action
# BE-GO-READY-AUTH-ENGINE-019 dep-import off facade.
from app.core.auth.dependencies import (
    get_current_user,
    require_not_esecutore,
    require_user_or_above,
    require_admin,
)
# BE-GO-READY-AUTH-ENGINE-013 Phase-7 contract fan-out: pure
# contract symbols (`CurrentUser`, `group_filter`) come from the
# shared contract module. DB-bound ownership helpers stay on
# `app.core.auth.ownership`. Facade KEEP_COMPAT preserved.
from app.shared.contracts.auth import CurrentUser, group_filter
from app.core.auth.ownership import (
    assert_group_access,
    assert_resource_visible,
    assert_can_modify,
    compute_can_edit,
)
from app.core.datasources.models import (
    DataSource,
    DataSourceCreate,
    DataSourceUpdate,
    DataSourceList,
    DataSourceTestResult,
    FileInfoResponse,
)
from app.core.datasources.interface import (
    DataSourceError,
    DataSourceConnectionError,
    DataSourceFileNotFoundError,
    DataSourcePermissionError,
)
from app.core.datasources.service import DataSourceService
from app.shared.sharing import visible_resource_ids

logger = get_logger(__name__)

router = APIRouter(prefix="/datasources", tags=["datasources"])

# Servizio inizializzato da main.py tramite init_datasources()
_service: Optional[DataSourceService] = None


def init_datasources(service: DataSourceService) -> None:
    """Inizializza il modulo. Chiamare da main.py dopo setup."""
    global _service
    _service = service


def _svc() -> DataSourceService:
    if _service is None:
        raise_typed_http(
            error_codes.SERVICE_NOT_INITIALIZED,
            status_code=500,
            message="DataSourceService non inizializzato",
        )
    return _service


def _ds_error_to_http(e: DataSourceError) -> HTTPException:
    """Converte eccezioni Data Source in HTTPException appropriate."""
    if isinstance(e, DataSourceFileNotFoundError):
        return HTTPException(404, str(e))
    if isinstance(e, DataSourcePermissionError):
        return HTTPException(403, str(e))
    if isinstance(e, DataSourceConnectionError):
        return HTTPException(502, str(e))
    return HTTPException(
        status_code=500,
        detail={
            "error": error_codes.INTERNAL_ERROR,
            "message": "Errore interno del server",
            "details": {},
        },
    )


# =============================================================================
# CRUD CONFIGURAZIONE
# =============================================================================

@router.post("", response_model=DataSource, status_code=201)
@audit_action(action="datasource.create")
def create_datasource(data: DataSourceCreate, current_user: CurrentUser = Depends(require_admin)):
    """Crea un nuovo Data Source. Solo admin/superadmin."""
    try:
        ds = _svc().create_datasource(
            data,
            group_id=current_user.group_id,
            created_by=current_user.user_id,
        )
        if ds:
            ds.can_edit = compute_can_edit(ds, current_user)
        return ds
    except Exception as e:
        logger.error(f"[CRASH] Errore creazione Data Source: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("", response_model=DataSourceList)
def list_datasources(current_user: CurrentUser = Depends(require_not_esecutore)):
    """Lista Data Source visibili: owned dal gruppo + condivisi via allowed_groups.
    Ogni record include `can_edit` calcolato per l'utente corrente."""
    is_super = current_user.role == "superadmin"

    all_resources = _svc().list_datasources(group_id=None)

    if not is_super:
        visible_ids = visible_resource_ids(
            'datasources',
            current_user.group_id,
            is_superadmin=False,
        )
        all_resources.datasources = [
            d for d in all_resources.datasources if d.id in visible_ids
        ]

    # Calcola can_edit per ogni record
    for ds in all_resources.datasources:
        ds.can_edit = compute_can_edit(ds, current_user)

    return all_resources


@router.get("/{ds_id}", response_model=DataSource)
def get_datasource(ds_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Dettaglio di un Data Source. Read-only su risorsa visibile (owned + shared)."""
    ds = _svc().get_datasource(ds_id)
    assert_resource_visible(ds, "datasources", current_user, "Data Source")
    if ds:
        ds.can_edit = compute_can_edit(ds, current_user)
    return ds


@router.put("/{ds_id}", response_model=DataSource)
@audit_action(action="datasource.update", target_param="ds_id")
def update_datasource(ds_id: str, data: DataSourceUpdate, current_user: CurrentUser = Depends(require_admin)):
    """Aggiorna un Data Source esistente. Solo admin/superadmin del gruppo, e
    solo se non creato da un superadmin (hierarchical ownership)."""
    existing = _svc().get_datasource(ds_id)
    assert_can_modify(existing, current_user, "Data Source")
    ds = _svc().update_datasource(ds_id, data)
    if ds:
        ds.can_edit = compute_can_edit(ds, current_user)
    return ds


@router.delete("/{ds_id}", status_code=204)
@audit_action(action="datasource.delete", target_param="ds_id")
def delete_datasource(ds_id: str, current_user: CurrentUser = Depends(require_admin)):
    """Elimina un Data Source e tutti i suoi dati. Solo admin/superadmin del gruppo,
    e solo se non creato da un superadmin (hierarchical ownership).

    Cascade check: rifiuta con 409 se il datasource è referenziato nel
    canvas di uno o più progetti (cerca nodi `datasource_node` con
    `datasource_id` matching).
    """
    existing = _svc().get_datasource(ds_id)
    assert_can_modify(existing, current_user, "Data Source")

    # Check dipendenze: progetti il cui canvas usa questo datasource.
    # Batch Fix 6.3: se il helper segnala che tutte le rows sono non
    # checkabili (RuntimeError sentinel), fail-closed 500 invece di
    # procedere silent con la delete.
    try:
        using_projects = _find_projects_using_datasource(ds_id)
    except RuntimeError as _check_err:
        logger.error(
            f"[CRASH] delete_datasource: pre-check cascade fallito per "
            f"{ds_id}: {_check_err}"
        )
        raise HTTPException(
            status_code=500,
            detail="Impossibile verificare dipendenze: riprova piu' tardi",
        )
    if using_projects:
        # PR-δ: filtrare dependencies per visibility lato chiamante.
        is_super = current_user.role == "superadmin"
        if is_super:
            visible = list(using_projects)
            hidden_count = 0
        else:
            visible = [p for p in using_projects if p.get("group_id") == current_user.group_id]
            hidden_count = len(using_projects) - len(visible)

        # Shape 409 cascade canonica (Batch Fix 6.1): dependencies con kind.
        # PR-δ: aggiunto `hidden_count` opzionale (FE backward-compatible).
        raise HTTPException(
            status_code=409,
            detail={
                "error": error_codes.DATASOURCE_IN_USE,
                "message": (
                    f"Data Source usato in {len(using_projects)} progetto/i. "
                    f"Rimuoverlo dai canvas prima di cancellarlo."
                ),
                "dependencies": [
                    {
                        "kind": "project",
                        "id": p["id"],
                        "name": p["name"],
                        "project_type": p.get("project_type", ""),
                    }
                    for p in visible
                ],
                "hidden_count": hidden_count,
            },
        )

    if not _svc().delete_datasource(ds_id):
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.DATASOURCE_NOT_FOUND,
                "message": f"Data Source '{ds_id}' non trovato",
            },
        )


def _find_projects_using_datasource(ds_id: str) -> List[Dict[str, str]]:
    """Cerca progetti il cui canvas contiene un nodo che referenzia ds_id.

    Fix N+1: era 1 + N query (list + get_project per ognuno). Ora fa UNA sola
    query che carica id, name, project_type, canvas in un solo colpo.

    Batch Fix 6.3: ogni per-row skip (JSON malformato, eccezione inattesa)
    emette un ``logger.warning`` breadcrumb con ``project_id`` (vincolo
    §5.1.4 kickoff: optional feature + breadcrumb identificante). Se
    *tutte* le rows falliscono il check, il helper solleva ``RuntimeError``
    per segnalare al caller che non e' stato possibile escludere
    dipendenze. Sollevamento intenzionale con built-in: sentinel interno
    al batch, non va esteso oltre questo scope.
    """
    import app.db.pg_connection as _db
    # PR-δ: includere group_id nella SELECT per consentire al caller di
    # filtrare le dipendenze visibili al chiamante (no leak nomi
    # cross-tenant nell'errore 409).
    rows = _db.fetchall("SELECT id, name, project_type, canvas, group_id FROM projects")
    using = []
    check_failures = 0
    for row in rows:
        row_id = row.get("id")
        try:
            canvas = row.get("canvas") or {}
            if isinstance(canvas, str):
                import json as _json
                try:
                    canvas = _json.loads(canvas)
                except Exception as _json_err:
                    logger.warning(
                        f"_find_projects_using_datasource: canvas JSON "
                        f"malformato per progetto {row_id}: {_json_err}"
                    )
                    check_failures += 1
                    continue
            nodes = canvas.get("nodes") or [] if isinstance(canvas, dict) else []
            for n in nodes:
                if not isinstance(n, dict):
                    continue
                top_ds = n.get("datasource_id")
                nested = n.get("data") or {}
                nested_ds = nested.get("datasource_id") if isinstance(nested, dict) else None
                if top_ds == ds_id or nested_ds == ds_id:
                    using.append({
                        "id": row["id"],
                        "name": row["name"],
                        "project_type": row.get("project_type", ""),
                        "group_id": row.get("group_id", ""),
                    })
                    break
        except Exception as _row_err:
            logger.warning(
                f"_find_projects_using_datasource: errore check canvas "
                f"progetto {row_id}: {_row_err}"
            )
            check_failures += 1
            continue

    # Fail-closed se TUTTE le rows hanno fallito il check: in quel caso
    # il helper non puo' dire se il datasource e' usato o no, e il caller
    # deve rifiutare la delete con 500 invece di procedere silent.
    if rows and check_failures == len(rows):
        raise RuntimeError(
            f"Impossibile verificare dipendenze datasource: "
            f"{check_failures} progetto/i non checkabili"
        )
    return using


@router.post("/{ds_id}/test", response_model=DataSourceTestResult)
@audit_action(action="datasource.test", target_param="ds_id")
def test_connection(ds_id: str, current_user: CurrentUser = Depends(require_not_esecutore)):
    """Testa la connessione di un Data Source. Read-only su risorsa visibile."""
    existing = _svc().get_datasource(ds_id)
    assert_resource_visible(existing, "datasources", current_user, "Data Source")
    # Bug #2 (2026-04-23): credenziale cifrata non decriptabile → 422,
    # non 200-OK fasullo ne' 500 ASGI.
    from app.shared.crypto import CredentialUnavailableError
    try:
        return _svc().test_connection(ds_id)
    except CredentialUnavailableError as e:
        raise HTTPException(status_code=422, detail=str(e))


# =============================================================================
# OPERAZIONI FILE
# =============================================================================

@router.get("/{ds_id}/files", response_model=list[FileInfoResponse])
def list_files(
    ds_id: str,
    path: str = Query(default="", description="Percorso relativo alla root"),
    pattern: Optional[str] = Query(default=None, description="Glob pattern (es. 'export_*.csv')"),
    include_hash: bool = Query(default=False, description="Calcola hash md5 per ogni file"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """Lista i file in un percorso del Data Source. Read-only su risorsa visibile."""
    assert_resource_visible(_svc().get_datasource(ds_id), "datasources", current_user, "Data Source")
    try:
        return _svc().list_files(ds_id, path=path, pattern=pattern, include_hash=include_hash)
    except DataSourceError as e:
        raise _ds_error_to_http(e)
    except Exception as e:
        logger.error(f"[CRASH] Errore list_files {ds_id}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/{ds_id}/files/download")
def download_file(
    ds_id: str,
    path: str = Query(..., description="Percorso relativo alla root"),
    current_user: CurrentUser = Depends(require_not_esecutore),
):
    """
    Scarica il contenuto raw di un file.
    Ritorna i bytes con Content-Type application/octet-stream.
    Il parsing (CSV, GeoJSON, ecc.) è responsabilità di MANIPOLA.
    Read-only su risorsa visibile.
    """
    assert_resource_visible(_svc().get_datasource(ds_id), "datasources", current_user, "Data Source")
    try:
        content = _svc().read_file(ds_id, path)
        # Sanitizza il filename per il Content-Disposition:
        # - basename only (no full path leak)
        # - rimuove caratteri non sicuri per il header HTTP (quote, CRLF)
        # - fallback "download" se vuoto
        raw_basename = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        safe_filename = "".join(
            c for c in raw_basename
            if c.isprintable() and c not in '"\\\r\n'
        )[:200]
        if not safe_filename:
            safe_filename = "download"
        return Response(
            content=content,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
        )
    except DataSourceError as e:
        raise _ds_error_to_http(e)
    except Exception as e:
        logger.error(f"[CRASH] Errore download {ds_id}/{path}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.post("/{ds_id}/files/upload", status_code=201)
@audit_action(action="datasource.file_upload", target_param="ds_id")
def upload_file(
    ds_id: str,
    path: str = Query(..., description="Percorso destinazione relativo alla root"),
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(require_user_or_above),
):
    """
    Carica un file nel Data Source.
    Il path di destinazione può includere sottocartelle (create automaticamente).
    """
    assert_group_access(_svc().get_datasource(ds_id), current_user, "Data Source")
    try:
        content = file.file.read()
        # Difesa malware: il file finisce su SharePoint/SFTP/FS esterno
        # condiviso e persistente. Block-only mode: qualsiasi formato
        # business legittimo passa, eseguibili (PE/ELF/Mach-O/shebang)
        # sono rifiutati con 400.
        from app.utils.mime_validator import (
            MimeMismatchError, validate_no_executable,
        )
        try:
            validate_no_executable(
                content=content, filename=file.filename or path,
            )
        except MimeMismatchError as e:
            logger.warning(
                f"Upload datasource rifiutato (MIME): ds={ds_id} "
                f"path={path!r} user={current_user.username}: {e}"
            )
            raise HTTPException(status_code=400, detail=str(e))
        _svc().write_file(ds_id, path, content)
        return {"ok": True, "path": path, "size": len(content)}
    except DataSourceError as e:
        raise _ds_error_to_http(e)
    except Exception as e:
        logger.error(f"[CRASH] Errore upload {ds_id}/{path}: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


