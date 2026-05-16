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


def _hydrate_local_cache(bundle_task_id: str) -> None:
    """R2 multi-host: se la cache locale e' vuota, scarica il bundle da S3.

    Side effect: scrive su `IMDF_BUNDLES_DIR/{task_id}/`. Dopo questa
    chiamata, il path torna valido per lettura locale da parte degli
    endpoint esistenti.

    No-op su cache hit (local dir popolata).
    Silent pass su errori: il codice downstream gestisce i 404 come prima.
    """
    _bundle_access.hydrate_local_cache(
        bundle_task_id,
        root=settings.IMDF_BUNDLES_DIR,
        logger=logger,
    )


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

    # Validazione bundle_task_id + path traversal guard
    bundle_dir = _safe_bundle_dir(request.bundle_task_id)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
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
    # Verifica group ownership: l'utente deve avere accesso al progetto
    # che ha generato questo bundle
    _assert_bundle_group_access(bundle_dir, current_user)

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
    # Validazione path traversal (l'helper solleva 400 se malformato
    # Non serve verificare exists qui: il validator gestisce il caso bundle
    # mancante come BUNDLE_MISSING nel report
    _val_bundle_dir = _safe_bundle_dir(request.bundle_task_id)
    # R2 multi-host: hydrate cache locale da S3 se necessario (D3 commit 2
    # parita' con /publish e /bundles/{id}). Fix bundle solo-S3 ->
    # BUNDLE_MISSING falso-positivo. Hydrate failure resta silent (warning
    # in _hydrate_local_cache); il report mantiene BUNDLE_MISSING come
    # error code unico (R5: nessun HYDRATE_FAILED
    _hydrate_local_cache(request.bundle_task_id)
    # Verifica group ownership (skip se il bundle non esiste ancora: in
    # quel caso il validator restituira' BUNDLE_MISSING nel report
    if _val_bundle_dir.exists() and _val_bundle_dir.is_dir():
        _assert_bundle_group_access(_val_bundle_dir, current_user)
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
    il canvas store dopo un refresh (altrimenti i modali del nodo result
    restano vuoti perche' `lastTaskResult` e' in memoria e non persistente).
    """
    bundle_dir = _safe_bundle_dir(bundle_task_id)
    # R2 multi-host: hydrate cache locale da S3 se necessario
    _hydrate_local_cache(bundle_task_id)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.BUNDLE_NOT_FOUND,
                "message": f"Bundle '{bundle_task_id}' non trovato (forse scaduto)",
            },
        )
    # Group ownership check
    _assert_bundle_group_access(bundle_dir, current_user)

    # D3 commit 3 (R3): site_id letto esclusivamente da plenora_metadata.json
    # scritto da persist dal valore autoritativo di `loader.ImdfBundle.site_id`
    # Nessuna scansione dir per derivarlo. Bundle senza meta o con meta senza
    # site_id (legacy malformati) -> response site_id=None, FE gestisce
    site_id: Optional[str] = None
    _meta_for_site = _imdf_bundle_store.read_metadata(bundle_dir)
    if _meta_for_site:
        site_id = _meta_for_site.get("site_id") or None

    manifest_path = bundle_dir / "manifest.json"
    validation_path = bundle_dir / "validation.json"
    imdf_dir = (bundle_dir / site_id) if site_id else None
    gdb_path = None
    ifc_path = None

    # Scan residuo solo per gdb_path/ifc_path (nomi file non nel meta come Path
    # Branch site_id/imdf_dir rimossa: derivati da meta (D3 commit 3
    for entry in bundle_dir.iterdir():
        if entry.is_dir() and entry.suffix == ".gdb":
            gdb_path = entry
        elif entry.is_file() and entry.suffix.lower() == ".ifc":
            ifc_path = entry

    def _read_json(p):
        try:
            return _json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
        except Exception as e:
            logger.warning(f"read_json {p}: {e}")
            return None

    manifest   = _read_json(manifest_path)
    validation = _read_json(validation_path)
    renderers  = _imdf_bundle_store.read_renderers(bundle_dir)

    # Carica geojson IMDF (ogni layer in un dict
    geojson_layers: Dict[str, Any] = {}
    if imdf_dir and imdf_dir.exists():
        for gj in sorted(imdf_dir.glob("*.geojson")):
            try:
                geojson_layers[gj.stem] = _json.loads(gj.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"geojson {gj.name}: {e}")

    # Lista file del bundle (come nel task result
    files_list = []
    for f in sorted(bundle_dir.rglob("*")):
        if f.is_file():
            try:
                files_list.append({
                    "path": str(f.relative_to(bundle_dir)).replace("\\", "/"),
                    "size": f.stat().st_size,
                })
            except Exception as _fe:
                # File sparito tra rglob e stat (cleanup concorrente) o
                # permessi: skippa il singolo entry, non-fatal per la response
                logger.debug(f"files_list skip {f.name}: {type(_fe).__name__}: {_fe}")

    return {
        "site_id":       site_id,
        "bundle_task_id": bundle_task_id,
        "manifest":      manifest,
        "validation":    validation,
        "renderers":     renderers,
        "geojson":       geojson_layers,
        "files":         files_list,
        "has_gdb":       gdb_path is not None,
        "has_ifc":       ifc_path is not None,
        "has_renderers": renderers is not None,
    }


@router.get("/bundles", response_model=List[ImdfBundleSummary])
def list_bundles(
    project_id: Optional[str] = None,
    current_user: CurrentUser = Depends(get_current_user),
):
    """Lista i bundle IMDF disponibili su disco con metadata base.

    Args:
        project_id: se valorizzato, filtra solo i bundle generati per quel
            progetto (letto da plenora_metadata.json dentro ogni bundle).
    """
    bundles_dir = settings.IMDF_BUNDLES_DIR

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

    # R2 multi-host: union task_id da local cache + S3 (fonte di verita'
    # Un nodo senza cache locale vede comunque i bundle generati da altri nodi
    task_ids_set: set = set()
    if bundles_dir.exists():
        for entry in bundles_dir.iterdir():
            if entry.is_dir():
                task_ids_set.add(entry.name)
    try:
        task_ids_set.update(_imdf_bundle_store.list_task_ids())
    except Exception as e:
        logger.warning(f"list_bundles: S3 lookup fallito (mostro solo cache locale): {e}")

    if not task_ids_set:
        return []

    # Ordino: prima per mtime local (veloce), fallback per nome. I bundle solo-S3
    # senza local cache finiscono in coda ordinati per nome
    def _sort_key(tid: str) -> tuple:
        local_path = bundles_dir / tid
        try:
            return (0, -local_path.stat().st_mtime)
        except OSError:
            return (1, tid)
    sorted_task_ids = sorted(task_ids_set, key=_sort_key)

    out: List[ImdfBundleSummary] = []
    now = _t.time()
    legacy_skipped = 0
    acl_skipped = 0
    for task_id in sorted_task_ids:
        entry = bundles_dir / task_id
        entry_local_exists = entry.is_dir()
        try:
            # Leggi plenora_metadata.json: prima da local cache (fast
            # poi fallback a S3 (multi-host: bundle generato su altro nodo
            meta: Dict[str, Any] = _imdf_bundle_store.read_metadata(entry) or {}
            if not meta:
                try:
                    _meta_from_s3 = _imdf_bundle_store.read_remote_metadata(task_id)
                    if _meta_from_s3:
                        meta = _meta_from_s3
                except Exception as _e:
                    logger.debug(f"list_bundles: S3 meta read failed for {task_id}: {_e}")

            bundle_pid = meta.get("project_id")

            # ACL: escludi bundle di progetti non visibili all'utente
            # eccetto legacy senza project_id, che non possiamo valutare
            if visible_project_ids is not None and bundle_pid:
                if bundle_pid not in visible_project_ids:
                    acl_skipped += 1
                    continue

            # Filtro project_id — se il filtro e' attivo, richiediamo che
            # il bundle abbia un project_id noto e matchi. Bundle legacy
            # senza plenora_metadata.json (project_id=None) vengono skippati
            # e il conteggio finale viene loggato una sola volta
            if project_id:
                if not bundle_pid:
                    legacy_skipped += 1
                    continue
                if bundle_pid != project_id:
                    continue

            # Size: calcolabile solo se in cache locale. Altrimenti None
            # mostrato come "—" dal frontend
            size_bytes = (
                sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                if entry_local_exists else None
            )

            # Fallback per bundle senza plenora_metadata (vecchi). Le euristiche
            # richiedono accesso locale ai file; su S3-only resta il valore meta
            has_gdb = meta.get("has_gdb")
            if has_gdb is None and entry_local_exists:
                has_gdb = any(p.is_dir() and p.suffix == ".gdb" for p in entry.iterdir())

            # D3 commit 3 (R3): site_id letto solo da meta (scritto da persist
            # dal valore autoritativo di loader.ImdfBundle.site_id). Fallback
            # iterdir rimosso: nessuna scansione cross-node; bundle senza
            # site_id in meta -> site_id=None nella response
            site_id = meta.get("site_id")

            # Counts layer IMDF: preferenza ai valori scritti in metadata
            # al save time — disponibili anche su S3-only). Fallback counting
            # lazy dei .geojson solo se local cache c'e'
            building_count = meta.get("building_count")
            level_count    = meta.get("level_count")
            unit_count     = meta.get("unit_count")
            if (building_count is None or level_count is None or unit_count is None) \
                    and site_id and entry_local_exists:
                site_dir = entry / site_id
                if site_dir.is_dir():
                    def _count_features(p: Path) -> Optional[int]:
                        try:
                            if not p.exists():
                                return None
                            data = _json.loads(p.read_text(encoding="utf-8"))
                            feats = data.get("features")
                            return len(feats) if isinstance(feats, list) else None
                        except Exception as _e:
                            logger.warning(f"list_bundles: count {p.name} fallito: {_e}")
                            return None
                    if building_count is None:
                        building_count = _count_features(site_dir / "building.geojson")
                    if level_count is None:
                        level_count = _count_features(site_dir / "level.geojson")
                    if unit_count is None:
                        unit_count = _count_features(site_dir / "unit.geojson")

            # age_hours: da mtime locale se c'e', altrimenti None
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
            f"plenora_metadata.json (filter project_id='{project_id}')"
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
    """Cancella una cartella bundle IMDF dal disco.

    - Validazione path-traversal via `_safe_bundle_dir`
    - Verifica group access via `_assert_bundle_group_access` (fail-closed
      sui bundle legacy senza metadata)
    - 404 se il bundle non esiste
    - Rimozione ricorsiva della directory
    """
    bundle_dir = _safe_bundle_dir(bundle_task_id)
    # R2 multi-host: hydrate per check metadata group-access
    # se il bundle e' solo su S3 e non nella cache locale del nodo corrente
    _hydrate_local_cache(bundle_task_id)
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(
            status_code=404,
            detail={
                "error": error_codes.BUNDLE_NOT_FOUND,
                "message": f"Bundle '{bundle_task_id}' non trovato",
            },
        )
    _assert_bundle_group_access(bundle_dir, current_user)
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
