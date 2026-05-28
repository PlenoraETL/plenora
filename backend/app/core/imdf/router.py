"""
router.py — Endpoint IMDF

Endpoint
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core.auth.dependencies import get_current_user
from app.shared.contracts.auth import CurrentUser
from app.shared.contracts.payloads import JsonObject, StringMap
from app.shared import error_codes
from app.shared.error_envelope import raise_typed_http
from app.observability.audit_action import audit_action
from app.core.imdf import project_access
from app.core.imdf import bundle_store as _imdf_bundle_store
from app.core.imdf import bundle_access as _bundle_access
from app.core.imdf import storage as _imdf_storage
from app.core.imdf.validator import validate_publish
from app.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/imdf", tags=["imdf"])


# ══════════════════════════════════════════════════════════════════════
# VALIDAZIONE PATH TRAVERSAL
# ══════════════════════════════════════════════════════════════════════
# Regex + path-traversal guard centralizzati nel bundle lifecycle boundary.
# Il wrapper locale resta per tradurre `ValueError` in `HTTPException 400`
# preservando la signature attesa dai call site interni del router.


def _safe_bundle_dir(bundle_task_id: str) -> Path:
    """Ritorna il path della cartella bundle validando contro path traversal.

    Thin wrapper attorno a `bundle_store.resolve_bundle_dir`: traduce
    `ValueError` in `HTTPException 400` con detail stabile.

    Raises:
        HTTPException 400 se l'id e' malformato o tenta di uscire dalla root.
    """
    return _bundle_access.safe_bundle_dir(
        bundle_task_id,
        root=settings.IMDF_BUNDLES_DIR,
    )


# Tombstone (QUERY-1 step 3): `_hydrate_local_cache` rimosso.
# Tombstone (current cleanup): `_assert_bundle_group_access(bundle_dir, user)`
# rimosso. Gli endpoint IMDF fanno ACL solo dal meta storage via
# `bundle_access.assert_bundle_group_access_from_meta`.


# ══════════════════════════════════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════════════════════════════════

class ImdfPublishRequest(BaseModel):
    """Request body per POST /api/imdf/publish."""
    bundle_task_id: str = Field(
        ..., description="task_id dell'arricchimento che ha prodotto il bundle IMDF"
    )
    connection_id: str = Field(
        ..., description="ID connessione ArcGIS"
    )
    fs_name: str = Field(
        ..., description="Nome Feature Service da creare / aggiornare"
    )
    folder: Optional[str] = Field(
        None, description="Cartella ArcGIS dove creare il FS (root se None)"
    )
    mode: str = Field(
        "replace",
        description="Modalita': 'replace' (default), 'append', 'upsert', 'update'",
    )
    apply_renderers: bool = Field(
        False, description="Applica stili del renderers.json (solo modalita' replace)"
    )
    match_columns: Optional[StringMap] = Field(
        None,
        description="Override per-layer delle colonne business-key (solo upsert). "
                    "Chiavi: Localita, OperaPrincipale, Livello, Locale, Details",
    )


class ImdfValidateRequest(BaseModel):
    """Request body per POST /api/imdf/validate-publish."""
    bundle_task_id: str
    connection_id: str
    fs_name: str
    mode: str = "replace"
    match_columns: Optional[StringMap] = None
    folder: Optional[str] = None


class ImdfPublishResponse(BaseModel):
    task_id: str
    status: str = "pending"
    message: str


class ImdfBundleSummary(BaseModel):
    task_id:     str
    project_id:  Optional[str] = None
    site_id:     Optional[str] = None
    has_gdb:     bool
    size_mb:     float
    age_hours:   Optional[float] = None
    saved_at:    Optional[str] = None
    validation_summary: Optional[JsonObject] = None
    # Conteggi layer IMDF da plenora_metadata.json. None se non determinabili.
    building_count: Optional[int] = None
    level_count:    Optional[int] = None
    unit_count:     Optional[int] = None


# ══════════════════════════════════════════════════════════════════════
# ENDPOINTS
# ══════════════════════════════════════════════════════════════════════

@router.post("/publish", response_model=ImdfPublishResponse)
# bundle_task_id e' nel body Pydantic, l'extractor accetta solo str/int da
# kwarg diretto (vedi audit_action.py:190): target=NULL
@audit_action(action="imdf.publish")
def publish_imdf(
    request: ImdfPublishRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Lancia un task Celery per pubblicare il bundle IMDF su ArcGIS.

    Il bundle deve essere stato persistito da un precedente arricchimento
    nello storage IMDF. Se il bundle non esiste (o e' scaduto), il task
    ritorna un errore.
    """
    # Valida mode
    if request.mode not in ("replace", "append", "upsert", "update"):
        raise HTTPException(
            status_code=400,
            detail=f"mode non valido: {request.mode}. Valori validi: replace, append, upsert, update",
        )

    # Path traversal guard sull'id.
    _safe_bundle_dir(request.bundle_task_id)

    # Bundle check + ACL solo da storage. Bundle senza meta su storage
    # falliscono 404 e richiedono re-upload.
    meta = _imdf_storage.get_meta_only(request.bundle_task_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.BUNDLE_NOT_FOUND,
                "message": (
                    f"Bundle IMDF '{request.bundle_task_id}' non trovato "
                    f"(potrebbe essere scaduto: TTL {settings.IMDF_BUNDLE_TTL_HOURS}h). "
                    f"Riavvia l'arricchimento per rigenerarlo."
                ),
            },
        )

    _bundle_access.assert_bundle_group_access_from_meta(
        meta, current_user, request.bundle_task_id, logger=logger,
    )

    publisher_config = {
        "connection_id":   request.connection_id,
        "folder":          request.folder,
        "fs_name":         request.fs_name,
        "mode":            request.mode,
        "apply_renderers": request.apply_renderers,
        "match_columns":   request.match_columns,
    }

    from app.core.imdf.publish_dispatcher import enqueue_imdf_publish

    result = enqueue_imdf_publish(
        bundle_task_id=request.bundle_task_id,
        publisher_config=publisher_config,
    )

    logger.info(
        f"[User {current_user.username}] IMDF publish task avviato: "
        f"{result.id} (bundle={request.bundle_task_id}, mode={request.mode}, "
        f"fs={request.fs_name})"
    )

    return ImdfPublishResponse(
        task_id=result.id,
        status="pending",
        message=f"Pubblicazione IMDF in coda ({request.mode})",
    )


@router.post("/validate-publish")
@audit_action(action="imdf.validate_publish")
def validate_publish_endpoint(
    request: ImdfValidateRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Esegue un controllo read-only PRIMA della pubblicazione.

    Ritorna un dict strutturato col report: layer coinvolti, schema compat,
    impatto della modalita' (replace/append/upsert/update) con delta per layer,
    data quality issues. Non fa NESSUNA modifica ne' sul bundle ne' sul FS
    ArcGIS — serve solo a dare all'utente il quadro prima di confermare.
    """
    if request.mode not in ("replace", "append", "upsert", "update"):
        raise HTTPException(
            status_code=400,
            detail=f"mode non valido: {request.mode}. Valori validi: replace, append, upsert, update",
        )
    # Path traversal guard sull'id.
    _safe_bundle_dir(request.bundle_task_id)
    # ACL check solo da storage.
    # Bundle totalmente assente: il validator emettera' BUNDLE_MISSING
    # nel report — no early-return qui per simmetria con la logica
    # current (il validator e' l'oracolo del missing bundle).
    _val_meta = _imdf_storage.get_meta_only(request.bundle_task_id)
    if _val_meta is not None:
        _bundle_access.assert_bundle_group_access_from_meta(
            _val_meta, current_user, request.bundle_task_id, logger=logger,
        )
    try:
        report = validate_publish(
            bundle_task_id=request.bundle_task_id,
            connection_id=request.connection_id,
            fs_name=request.fs_name,
            mode=request.mode,
            match_columns=request.match_columns,
            folder=request.folder,
        )
        return report
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"validate_publish error: {e}", exc_info=True)
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")


@router.get("/bundles/{bundle_task_id}")
def get_bundle_metadata(
    bundle_task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Ritorna i metadata completi di un bundle IMDF persistito (manifest,
    validation, renderers, geojson, site_id) — stessa struttura che il task
    di enrichment salva in `result.imdf`. Usato dal frontend per rehydratare
    il canvas store dopo un refresh.

    IMDF-4: storage-first. L'endpoint legge meta/manifest/validation/
    renderers/geojson direttamente da object storage via
    ``imdf.storage.read_*``. Niente piu' ``ensure_local_bundle`` + scan
    filesystem per la response normale. Il ``has_gdb``/``has_ifc`` viene
    dai flag nel meta (scritti da persist al save time).
    """
    # Path traversal guard sull'id.
    _safe_bundle_dir(bundle_task_id)

    # Lettura solo da storage. Bundle senza layout per-file su storage
    # falliscono 404.
    meta = _imdf_storage.get_meta_only(bundle_task_id)

    if meta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.BUNDLE_NOT_FOUND,
                "message": f"Bundle '{bundle_task_id}' non trovato (forse scaduto)",
            },
        )

    _bundle_access.assert_bundle_group_access_from_meta(
        meta, current_user, bundle_task_id, logger=logger,
    )

    site_id: Optional[str] = meta.get("site_id") or None

    manifest   = _imdf_storage.read_manifest(bundle_task_id)
    validation = _imdf_storage.read_validation(bundle_task_id)
    renderers  = _imdf_storage.read_renderers(bundle_task_id)

    storage_files = _imdf_storage.list_bundle_file_entries(bundle_task_id)
    files_list: List[JsonObject] = []
    geojson_layers: Dict[str, Any] = {}
    site_prefix = f"{site_id}/" if site_id else None
    for entry in sorted(storage_files, key=lambda e: e["path"]):
        rel = entry["path"]
        files_list.append({"path": rel, "size": entry["size"]})
        if (
            site_prefix
            and rel.startswith(site_prefix)
            and rel.endswith(".geojson")
        ):
            layer_name = Path(rel).stem
            obj = _imdf_storage.read_geojson(bundle_task_id, rel)
            if obj is not None:
                geojson_layers[layer_name] = obj

    return {
        "site_id":       site_id,
        "bundle_task_id": bundle_task_id,
        "manifest":      manifest,
        "validation":    validation,
        "renderers":     renderers,
        "geojson":       geojson_layers,
        "files":         files_list,
        "has_gdb":       bool(meta.get("has_gdb")),
        "has_ifc":       bool(meta.get("has_ifc")),
        "has_renderers": renderers is not None,
    }


@router.get("/bundles", response_model=List[ImdfBundleSummary])
def list_bundles(
    project_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Lista i bundle IMDF disponibili con metadata base.

    IMDF-4: storage-first symmetrico a GET /bundles/{id}. La fonte di
    verita' e' object storage (`imdf.storage.list_task_ids`); il meta
    per-bundle e' letto via `imdf.storage.get_meta_only(task_id)`.
    Niente piu' `bundles_dir.iterdir()` + per-task `read_metadata` dal
    filesystem locale: la lista e' storage-only.

    Args:
        project_id: se valorizzato, filtra solo i bundle generati per quel
            progetto (letto da plenora_metadata dentro ogni bundle).
    """
    # Precarico i progetti visibili all'utente (per filtrare i bundle di
    # altri gruppi). superadmin vede tutto → skip del check
    # Fail-CLOSED: in caso di errore ACL rispondiamo 500 senza servire
    # dati. Un fallback `visible_project_ids = None` disabiliterebbe
    # del tutto il filtro al loop successivo, esponendo i bundle di
    # tutti i gruppi a chi non e' superadmin
    visible_project_ids: Optional[set] = None
    if current_user.role != "superadmin":
        try:
            visible_project_ids = project_access.list_project_ids_visible_to_group(
                current_user.group_id,
            )
        except Exception as e:
            logger.error(
                f"list_bundles: calcolo visible_project_ids fallito: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail="Errore verifica ACL bundle IMDF",
            )

    # Storage-only: task_id list da object storage (fonte di verita').
    try:
        task_ids = _imdf_storage.list_task_ids()
    except Exception as e:
        logger.warning(f"list_bundles: storage lookup fallito: {e}")
        task_ids = []

    if not task_ids:
        return []

    # Ordino: i bundle con saved_at piu' recente in testa. Per chi non ha
    # saved_at, fallback alphabetico stabile. saved_at viene letto dal
    # meta in storage.
    out: List[ImdfBundleSummary] = []
    acl_skipped = 0

    # Pre-fetch meta per ogni task_id (single round-trip per bundle).
    meta_by_task: Dict[str, Dict[str, Any]] = {}
    for tid in task_ids:
        try:
            remote = _imdf_storage.get_meta_only(tid)
        except Exception as e:
            logger.debug(f"list_bundles: storage meta read fail {tid}: {e}")
            remote = None
        meta_by_task[tid] = remote or {}

    def _sort_key(tid: str) -> tuple:
        m = meta_by_task.get(tid) or {}
        saved_at = m.get("saved_at") or ""
        # saved_at vuoto -> alla fine, ordinato per task_id
        return (0, -1 * len(saved_at), saved_at) if saved_at else (1, 0, tid)
    sorted_task_ids = sorted(set(task_ids), key=_sort_key)

    for task_id in sorted_task_ids:
        try:
            meta = meta_by_task.get(task_id) or {}
            bundle_pid = meta.get("project_id")
            if not meta:
                logger.debug(f"list_bundles: skip {task_id}: meta assente")
                continue

            # ACL: escludi bundle di progetti non visibili all'utente
            if visible_project_ids is not None and bundle_pid:
                if bundle_pid not in visible_project_ids:
                    acl_skipped += 1
                    continue

            if project_id:
                if not bundle_pid:
                    continue
                if bundle_pid != project_id:
                    continue

            storage_files = _imdf_storage.list_bundle_file_entries(task_id)
            size_bytes = sum(int(f.get("size") or 0) for f in storage_files)

            has_gdb = meta.get("has_gdb")
            site_id = meta.get("site_id")

            building_count = meta.get("building_count")
            level_count    = meta.get("level_count")
            unit_count     = meta.get("unit_count")
            if (
                site_id
                and (
                    building_count is None
                    or level_count is None
                    or unit_count is None
                )
            ):
                fallback_counts: dict[str, int] = {}
                site_prefix = f"{site_id}/"
                wanted = {"building", "level", "unit"}
                for file_entry in storage_files:
                    rel = file_entry.get("path") or ""
                    if not rel.startswith(site_prefix) or not rel.endswith(".geojson"):
                        continue
                    layer_name = Path(rel).stem
                    if layer_name not in wanted:
                        continue
                    obj = _imdf_storage.read_geojson(task_id, rel)
                    if isinstance(obj, dict):
                        features = obj.get("features")
                        if isinstance(features, list):
                            fallback_counts[layer_name] = len(features)
                if building_count is None:
                    building_count = fallback_counts.get("building")
                if level_count is None:
                    level_count = fallback_counts.get("level")
                if unit_count is None:
                    unit_count = fallback_counts.get("unit")

            out.append(ImdfBundleSummary(
                task_id=task_id,
                project_id=meta.get("project_id"),
                site_id=site_id,
                has_gdb=bool(has_gdb) if has_gdb is not None else False,
                size_mb=round(size_bytes / (1024 * 1024), 2) if size_bytes else 0.0,
                age_hours=None,
                saved_at=meta.get("saved_at"),
                validation_summary=meta.get("validation_summary"),
                building_count=building_count,
                level_count=level_count,
                unit_count=unit_count,
            ))
        except Exception as e:
            logger.warning(f"list_bundles: skip {task_id}: {e}")

    if acl_skipped > 0:
        logger.info(
            f"list_bundles: skippati {acl_skipped} bundle per ACL group "
            f"(utente='{current_user.username}')"
        )
    return out


@router.delete("/bundles/{bundle_task_id}", status_code=204)
@audit_action(action="imdf.bundle.delete", target_param="bundle_task_id")
def delete_bundle(
    bundle_task_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Cancella un bundle IMDF da storage canonico e directory locale.

    ACL check storage-first: bundle senza meta su storage falliscono 404.

    - Validazione path-traversal via `_safe_bundle_dir`
    - Verifica group access via `assert_bundle_group_access_from_meta`
    - 404 se il bundle non esiste su storage
    - Rimozione da storage + cleanup directory locale
    """
    bundle_dir = _safe_bundle_dir(bundle_task_id)
    meta = _imdf_storage.get_meta_only(bundle_task_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.BUNDLE_NOT_FOUND,
                "message": f"Bundle '{bundle_task_id}' non trovato",
            },
        )
    _bundle_access.assert_bundle_group_access_from_meta(
        meta, current_user, bundle_task_id, logger=logger,
    )
    try:
        _imdf_bundle_store.delete_bundle(
            task_id=bundle_task_id,
            local_dir=bundle_dir,
        )
    except (OSError, RuntimeError, ValueError) as e:
        logger.error(
            f"delete_bundle: rimozione '{bundle_task_id}' fallita: {e}",
            exc_info=True,
        )
        raise_typed_http(error_codes.INTERNAL_ERROR, status_code=500, message="Errore interno del server")

    logger.info(
        f"[User {current_user.username}] IMDF bundle '{bundle_task_id}' cancellato"
    )
    # F2-B3 (2026-04-24): DELETE risponde 204 No Content, body vuoto
