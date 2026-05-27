"""
router.py — Endpoint IMDF

Endpoint
"""

import json as _json
import time as _t

from pathlib import Path
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.core.auth.dependencies import get_current_user
from app.core.auth.ownership import assert_resource_visible
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
# Era helper per idratare la cache locale da storage prima del check
# ACL legacy. Post IMDF-4 step 4 (ACL via meta on storage), zero
# caller dagli endpoint. Dead code.


def _assert_bundle_group_access(bundle_dir: Path, current_user: CurrentUser) -> None:
    """Verifica che l'utente abbia accesso al progetto che ha generato
    questo bundle IMDF.

    Policy fail-closed:
      - superadmin → bypass
      - `plenora_metadata.json` assente / illeggibile / senza
        `project_id` → 403 (bundle privi di ownership info non sono
        accessibili: rigenerare la pipeline per ottenerne uno valido)
      - `project_id` presente ma progetto non in DB → 404
      - `project_id` presente → check standard owned+shared via
        `assert_resource_visible`

    Raises HTTPException 403/404 se accesso non consentito.
    """
    _bundle_access.assert_bundle_group_access(
        bundle_dir,
        current_user,
        logger=logger,
        visible_checker=assert_resource_visible,
    )


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
    # Conteggi layer IMDF (da plenora_metadata.json o fallback dal
    # counting dei .geojson). None se non determinabili
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
    (enrichment_task salva in IMDF_BUNDLES_DIR/{task_id}/). Se il bundle non
    esiste (o e' scaduto), il task ritorna un errore.
    """
    # Valida mode
    if request.mode not in ("replace", "append", "upsert", "update"):
        raise HTTPException(
            status_code=400,
            detail=f"mode non valido: {request.mode}. Valori validi: replace, append, upsert, update",
        )

    # Path traversal guard sull'id.
    _safe_bundle_dir(request.bundle_task_id)

    # IMDF-4 step 4: bundle check + ACL solo da storage. Post-tombstone
    # il fallback locale (`_hydrate_local_cache + read_metadata`) e'
    # rimosso: i bundle pre-IMDF-3 senza meta su storage falliscono
    # 404 e richiedono re-upload.
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
    # IMDF-4 step 4: ACL check solo da storage (no fallback locale).
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
    dai flag nel meta (scritti da persist al save time). Fallback su
    cache locale solo se il meta su storage manca (bundle legacy in zip
    layout, hydrated dal `_hydrate_local_cache`).
    """
    # Path traversal guard sull'id.
    _safe_bundle_dir(bundle_task_id)

    # IMDF-4 step 4: lettura solo da storage. Post-tombstone il fallback
    # legacy (`_hydrate_local_cache + bundle_dir.rglob`) e' rimosso.
    # Bundle pre-IMDF-3 senza layout per-file su storage falliscono 404.
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
    filesystem locale per il path normale. La cache locale resta una
    fallback union per i bundle pre-IMDF-3 ancora solo su disco
    (es. ambienti dev con storage off).

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

    # Storage-first: task_id list da object storage (fonte di verita').
    # Fallback union con cache locale per i bundle pre-IMDF-3 ancora
    # solo on-disk (dev senza S3 / bundle legacy mai migrati).
    task_ids_set: set = set()
    try:
        task_ids_set.update(_imdf_bundle_store.list_task_ids())
    except Exception as e:
        logger.warning(
            f"list_bundles: storage lookup fallito (uso solo cache locale): {e}"
        )
    bundles_dir = settings.IMDF_BUNDLES_DIR
    if bundles_dir.exists():
        for entry in bundles_dir.iterdir():
            if entry.is_dir():
                task_ids_set.add(entry.name)

    if not task_ids_set:
        return []

    # Ordino: i bundle con saved_at piu' recente in testa. Per chi non ha
    # saved_at, fallback alphabetico stabile. saved_at viene letto dal
    # meta in storage; e' indipendente dalla cache locale (multi-host safe).
    out: List[ImdfBundleSummary] = []
    now = _t.time()
    legacy_skipped = 0
    acl_skipped = 0

    # Pre-fetch meta per ogni task_id (single round-trip per bundle).
    # In ambiente prod questo e' O(N) GETs su storage; cache locale
    # cortocircuita il path quando disponibile.
    meta_by_task: Dict[str, Dict[str, Any]] = {}
    for tid in task_ids_set:
        local_entry = bundles_dir / tid
        cached_meta = (
            _imdf_bundle_store.read_metadata(local_entry)
            if local_entry.is_dir() else None
        )
        if cached_meta:
            meta_by_task[tid] = cached_meta
            continue
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
    sorted_task_ids = sorted(task_ids_set, key=_sort_key)

    for task_id in sorted_task_ids:
        try:
            meta = meta_by_task.get(task_id) or {}
            entry = bundles_dir / task_id
            entry_local_exists = entry.is_dir()
            bundle_pid = meta.get("project_id")

            # ACL: escludi bundle di progetti non visibili all'utente
            # eccetto legacy senza project_id, che non possiamo valutare
            if visible_project_ids is not None and bundle_pid:
                if bundle_pid not in visible_project_ids:
                    acl_skipped += 1
                    continue

            # Filtro project_id — bundle legacy senza meta vengono skippati
            # quando il filtro e' attivo.
            if project_id:
                if not bundle_pid:
                    legacy_skipped += 1
                    continue
                if bundle_pid != project_id:
                    continue

            # Size: calcolabile solo se in cache locale (storage non
            # espone size del bundle aggregato). Su solo-storage resta 0.
            size_bytes = (
                sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                if entry_local_exists else None
            )

            has_gdb = meta.get("has_gdb")
            if has_gdb is None and entry_local_exists:
                # Legacy bundle senza meta: fallback iterdir locale.
                has_gdb = any(
                    p.is_dir() and p.suffix == ".gdb" for p in entry.iterdir()
                )

            site_id = meta.get("site_id")

            building_count = meta.get("building_count")
            level_count    = meta.get("level_count")
            unit_count     = meta.get("unit_count")
            if entry_local_exists and site_id and (
                building_count is None or level_count is None or unit_count is None
            ):
                site_dir = entry / str(site_id)

                def _count_geojson_features(name: str) -> int | None:
                    path = site_dir / f"{name}.geojson"
                    if not path.is_file():
                        return None
                    try:
                        data = _json.loads(path.read_text(encoding="utf-8"))
                    except (_json.JSONDecodeError, OSError, UnicodeError):
                        return None
                    features = data.get("features") if isinstance(data, dict) else None
                    return len(features) if isinstance(features, list) else None

                building_count = (
                    building_count
                    if building_count is not None
                    else _count_geojson_features("building")
                )
                level_count = (
                    level_count
                    if level_count is not None
                    else _count_geojson_features("level")
                )
                unit_count = (
                    unit_count
                    if unit_count is not None
                    else _count_geojson_features("unit")
                )

            # age_hours: da mtime locale se c'e', altrimenti None.
            # saved_at del meta sarebbe la fonte canonica; lasciamo
            # mtime per backward-compat finche' il FE non legge saved_at.
            age_hours = (
                round((now - entry.stat().st_mtime) / 3600, 2)
                if entry_local_exists else None
            )

            out.append(ImdfBundleSummary(
                task_id=task_id,
                project_id=meta.get("project_id"),
                site_id=site_id,
                has_gdb=bool(has_gdb) if has_gdb is not None else False,
                size_mb=round(size_bytes / (1024 * 1024), 2) if size_bytes else 0.0,
                age_hours=age_hours,
                saved_at=meta.get("saved_at"),
                validation_summary=meta.get("validation_summary"),
                building_count=building_count,
                level_count=level_count,
                unit_count=unit_count,
            ))
        except Exception as e:
            logger.warning(f"list_bundles: skip {task_id}: {e}")

    if legacy_skipped > 0:
        logger.info(
            f"list_bundles: skippati {legacy_skipped} bundle legacy senza "
            f"plenora_metadata (filter project_id='{project_id}')"
        )
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
    """Cancella un bundle IMDF da storage canonico + cache locale.

    IMDF-4 step 4: ACL check storage-first (no hydrate locale per
    leggere meta). Bundle pre-IMDF-3 senza meta su storage falliscono
    404 — il delete di cache locale eventualmente presente puo' essere
    fatto manualmente da admin.

    - Validazione path-traversal via `_safe_bundle_dir`
    - Verifica group access via `assert_bundle_group_access_from_meta`
    - 404 se il bundle non esiste su storage
    - Rimozione da storage + cleanup cache locale
    """
    bundle_dir = _safe_bundle_dir(bundle_task_id)
    meta = _imdf_storage.get_meta_only(bundle_task_id)
    if meta is None and bundle_dir.is_dir():
        meta = _imdf_bundle_store.read_metadata(bundle_dir)
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
