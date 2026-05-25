"""
Storage IMDF bundle su object storage cloud-agnostic.

Chiude R2 del multi-host audit: i bundle IMDF non vivono piu' solo su
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import List, Optional

from app.infrastructure.storage.cloud_backends import object_storage_enabled
from app.shared.contracts.payloads import JsonObject, StringMap
from app.shared.resources.file_cleanup import rmtree_safe
from app.shared.resources.storage_layout import IMDF_BUNDLES_PREFIX
from app.utils.zip_safe import safe_extractall
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Module-level alias kept for internal call sites and tests.
PREFIX = IMDF_BUNDLES_PREFIX
META_SUFFIX = ".meta.json"
ZIP_SUFFIX = ".zip"


def _storage():
    """Lazy load della factory cloud runtime.

    Solleva `ObjectStorageDisabledError` se la capability globale e' off:
    i caller list/get_meta_only/list_task_ids degradano graceful
    (cache locale only) catturando l'eccezione; save_bundle invece
    deve fallire perche' il pubblico richiede deve completare l'upload."""
    from app.infrastructure.storage.cloud_backends import get_runtime_object_storage
    return get_runtime_object_storage()


def _zip_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{ZIP_SUFFIX}"


def _meta_key(task_id: str) -> str:
    return f"{PREFIX}/{task_id}{META_SUFFIX}"


def _files_prefix(task_id: str) -> str:
    """Storage key prefix per i file individuali del bundle (IMDF-3).

    Layout: ``{PREFIX}/{task_id}/files/{filename}``. Permette di
    servire singoli componenti del bundle senza scaricare lo zip
    intero (IMDF-4 endpoint serve).
    """
    return f"{PREFIX}/{task_id}/files"


def _file_key(task_id: str, name: str) -> str:
    return f"{_files_prefix(task_id)}/{name}"


def _gdb_zip_key(task_id: str) -> str:
    """Storage key del `.gdb` zippato (adapter legacy: il File
    Geodatabase resta directory-format per pyogrio, lo manteniamo
    come singolo zip per upload/download atomico)."""
    return f"{PREFIX}/{task_id}/_gdb.zip"


def _local_dir(task_id: str) -> Path:
    from app.config import settings as _settings
    return _settings.IMDF_BUNDLES_DIR / task_id




def save_bundle_files(
    task_id: str,
    files: dict[str, bytes],
    meta: JsonObject,
    *,
    gdb_zip_bytes: Optional[bytes] = None,
    ifc_bytes: Optional[bytes] = None,
    ifc_filename: Optional[str] = None,
) -> StringMap:
    """Upload RAM-first dei componenti di un ImdfBundle (IMDF-3).

    Sostituisce la sequenza legacy ``persist_imdf_bundle_to_disk +
    save_bundle(local_dir)``: niente piu' workspace fisico, niente
    zip monolitico. Ogni componente in ``files`` (manifest.json,
    validation.json, renderers.json, ``{site_id}/*.geojson``) va su
    storage come oggetto individuale al key ``{PREFIX}/{task_id}/files/{name}``.

    Il ``.gdb`` resta directory-format (pyogrio): viene zippato dal
    caller (worker o persist helper) e passato come ``gdb_zip_bytes``,
    uploaded come singolo oggetto ``{PREFIX}/{task_id}/_gdb.zip``.

    Il ``.ifc`` (binario opzionale) puo' essere passato come
    ``ifc_bytes`` + ``ifc_filename`` e va su ``{PREFIX}/{task_id}/files/{ifc_filename}``.

    Args:
        task_id: id univoco del bundle.
        files: mapping ``{filename: bytes}`` dei componenti RAM-first.
        meta: dict di metadata (uploaded come ``{task_id}.meta.json``).
        gdb_zip_bytes: bytes dello zip del .gdb (None se has_gdb=False).
        ifc_bytes: bytes del .ifc (None se has_ifc=False).
        ifc_filename: nome del file .ifc (richiesto se ifc_bytes!=None).

    Returns:
        dict con chiavi:
          - file_keys: list[str] dei key uploaded
          - meta_key: key del meta json
          - gdb_zip_key: key del .gdb zip se uploaded, altrimenti None

    Raises:
        OSError / RuntimeError dal backend storage (propagati).
    """
    storage = _storage()
    file_keys: list[str] = []

    for name, data in files.items():
        key = _file_key(task_id, name)
        storage.upload_bytes(data, key)
        file_keys.append(key)
    logger.info(
        f"[{task_id}] imdf {len(file_keys)} files uploaded under {_files_prefix(task_id)}/"
    )

    gdb_zip_key: Optional[str] = None
    if gdb_zip_bytes is not None:
        gdb_zip_key = _gdb_zip_key(task_id)
        storage.upload_bytes(gdb_zip_bytes, gdb_zip_key)
        logger.info(
            f"[{task_id}] imdf .gdb zip uploaded: {gdb_zip_key} "
            f"({len(gdb_zip_bytes)} bytes)"
        )

    if ifc_bytes is not None:
        if not ifc_filename:
            raise ValueError(
                "save_bundle_files: ifc_filename obbligatorio quando ifc_bytes!=None"
            )
        ifc_key = _file_key(task_id, ifc_filename)
        storage.upload_bytes(ifc_bytes, ifc_key)
        file_keys.append(ifc_key)
        logger.info(f"[{task_id}] imdf .ifc uploaded: {ifc_key}")

    meta_key = _meta_key(task_id)
    meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
    storage.upload_bytes(meta_bytes, meta_key)
    logger.info(f"[{task_id}] imdf meta uploaded: {meta_key}")

    return {
        "file_keys": file_keys,
        "meta_key": meta_key,
        "gdb_zip_key": gdb_zip_key,
    }


def read_bundle_file_bytes(task_id: str, name: str) -> Optional[bytes]:
    """Legge un singolo file del bundle direttamente da storage (IMDF-3).

    Pensato per i reader che servono manifest/validation/renderers/geojson
    senza dover prima idratare l'intero bundle in cache locale
    (IMDF-4 endpoint serve). Ritorna ``None`` se l'oggetto non
    esiste su storage o la capability object_storage e' off.
    """
    if not object_storage_enabled():
        return None
    storage = _storage()
    key = _file_key(task_id, name)
    try:
        stream = storage.get_object_stream(key)
    except Exception as e:
        logger.debug(f"[{task_id}] read_bundle_file_bytes {name!r}: {e}")
        return None
    return b"".join(stream.get("body", []))


def save_bundle(task_id: str, local_dir: Path, meta: Optional[JsonObject] = None) -> StringMap:
    """Zippa `local_dir` (bundle estratto dal worker) e carica su S3.

    Upload anche il `plenora_metadata.json` separatamente come
    `{task_id}.meta.json` per list endpoint veloci (no download zip).

    Args:
        task_id: id univoco per l'oggetto S3
        local_dir: cartella gia' popolata dal persist locale
        meta: dict di metadata (se None, lo legge da `plenora_metadata.json`
            nella dir)

    Returns:
        dict con chiavi:
          - zip_key: key S3 dello zip
          - meta_key: key S3 del meta

    Raises:
        FileNotFoundError: local_dir non esiste
        OSError: zip/upload fallito (propagato)
    """
    if not local_dir.exists() or not local_dir.is_dir():
        raise FileNotFoundError(f"Bundle dir non trovata: {local_dir}")

    # Leggi meta se non fornito (usa file scritto dal persist step precedente
    if meta is None:
        meta_path = local_dir / "plenora_metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"[{task_id}] meta parse fallito: {e}; upload senza meta.json")
                meta = None

    storage = _storage()

    # Build ZIP in-memory via BytesIO, upload diretto via upload_bytes.
    # I bundle IMDF tipici sono 20-100MB; entrano in RAM senza problemi.
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in local_dir.rglob("*"):
            if p.is_file():
                arcname = p.relative_to(local_dir)
                zf.write(p, arcname=str(arcname))
    zip_bytes = zip_buf.getvalue()

    zk = _zip_key(task_id)
    storage.upload_bytes(zip_bytes, zk)
    logger.info(
        f"[{task_id}] imdf bundle zip uploaded: {zk} ({len(zip_bytes)} bytes)"
    )

    # Meta JSON separato (se disponibile) — utile per list veloce
    mk = _meta_key(task_id)
    if meta is not None:
        meta_bytes = json.dumps(meta, indent=2).encode("utf-8")
        storage.upload_bytes(meta_bytes, mk)
        logger.info(f"[{task_id}] imdf meta uploaded: {mk}")

    return {"zip_key": _zip_key(task_id), "meta_key": mk}


# Read (endpoint side


def ensure_local_bundle(task_id: str) -> Path:
    """Garantisce che il bundle sia estratto localmente. Cache miss → pull da storage.

    IMDF-3: il layout su storage e' cambiato — ora i componenti
    individuali (manifest/validation/renderers/geojson + .ifc)
    vivono sotto ``{PREFIX}/{task_id}/files/*`` come oggetti singoli,
    e il ``.gdb`` (se presente) e' uno zip dedicato a
    ``{PREFIX}/{task_id}/_gdb.zip``. Cache miss → list+download dei
    file individuali (+ extract del .gdb zip).

    Policy:
      - Se `IMDF_BUNDLES_DIR/{task_id}/` esiste e non e' vuota → cache hit.
      - Altrimenti → list_objects su files prefix → download each → write
        in local_dir. Se ``_gdb.zip`` presente → download + extract.
      - Se ne' files ne' _gdb.zip esistono su storage → FileNotFoundError.

    Side effect: scrive su IMDF_BUNDLES_DIR locale. Concorrenza multi-process
    sullo stesso task_id sul medesimo nodo e' rara (ogni task_id e' univoco
    e il primo accessor vince).

    Returns:
        Path della directory locale con il bundle estratto.

    Raises:
        FileNotFoundError: bundle non presente ne' in local ne' su storage.
    """
    local = _local_dir(task_id)
    if local.exists() and any(local.iterdir()):
        return local

    storage = _storage()
    prefix = _files_prefix(task_id)
    try:
        file_objects = storage.list_objects(prefix + "/")
    except Exception as e:
        logger.warning(f"[{task_id}] imdf storage list_objects failed: {e}")
        file_objects = []

    has_gdb = False
    try:
        # list_objects sul gdb_zip_key per detect se esiste l'adapter .gdb
        gdb_objects = storage.list_objects(_gdb_zip_key(task_id))
        has_gdb = any(o.get("key") == _gdb_zip_key(task_id) for o in gdb_objects)
    except Exception as e:
        logger.debug(f"[{task_id}] gdb zip probe failed: {e}")

    if not file_objects and not has_gdb:
        # Fallback compat con bundle persistiti con `save_bundle` legacy
        # (zip monolitico). Permette di leggere bundle creati prima di
        # IMDF-3 senza forzare un re-upload.
        zk = _zip_key(task_id)
        try:
            obj = storage.get_object_stream(zk)
        except Exception as e:
            logger.warning(
                f"[{task_id}] imdf storage hydration failed "
                f"(no files-layout, no legacy zip): {e}"
            )
            raise FileNotFoundError(
                f"Bundle {task_id} non presente su storage "
                f"(prefix={prefix} vuoto, no _gdb.zip, no {zk})"
            )
        zip_bytes = b"".join(obj.get("body", []))
        local.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            safe_extractall(zf, local)
        logger.info(
            f"[{task_id}] imdf bundle hydrated from storage (legacy zip layout) -> {local}"
        )
        return local

    local.mkdir(parents=True, exist_ok=True)

    # Hydrate individual files: list_objects ritorna key tipo
    # `{prefix}/{name}` con name che puo' includere `/` (geojson sotto
    # site_id). Strip del prefix per ricostruire il path relativo.
    prefix_strip = prefix + "/"
    for obj in file_objects:
        key = obj.get("key")
        if not key or not key.startswith(prefix_strip):
            continue
        rel_name = key[len(prefix_strip):]
        if not rel_name:
            continue
        stream = storage.get_object_stream(key)
        data = b"".join(stream.get("body", []))
        target = local / rel_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    if has_gdb:
        gdb_stream = storage.get_object_stream(_gdb_zip_key(task_id))
        gdb_bytes = b"".join(gdb_stream.get("body", []))
        # I bundle IMDF tipici sono 20-100MB; entrano in RAM senza problemi.
        with zipfile.ZipFile(io.BytesIO(gdb_bytes), "r") as zf:
            safe_extractall(zf, local)
        logger.info(f"[{task_id}] .gdb extracted into {local}")

    logger.info(
        f"[{task_id}] imdf bundle hydrated from storage (files-layout) -> {local}"
    )
    return local


def get_meta_only(task_id: str) -> Optional[JsonObject]:
    """Legge SOLO `plenora_metadata.json` da S3 (senza download zip).

    Usato dal list endpoint per enumerare bundle senza costi di estrazione.
    Ritorna None se il meta non esiste su S3 (bundle non ancora pubblicato
    o solo local-cached) oppure se la capability object storage e' off.
    """
    if not object_storage_enabled():
        return None
    storage = _storage()
    mk = _meta_key(task_id)
    try:
        stream = storage.get_object_stream(mk)
        body_bytes = b"".join(stream["body"])
        return json.loads(body_bytes.decode("utf-8"))
    except Exception as e:
        logger.debug(f"[{task_id}] meta only read skipped: {e}")
        return None


def list_task_ids() -> List[str]:
    """Elenca task_id dei bundle presenti su S3 (via meta.json).

    Usato dal list endpoint per vedere bundle da tutti i nodi (non solo
    quelli cached sul nodo corrente). ritorna [] se la
    capability object storage e' disabilitata (router fallback su
    cache locale only)."""
    if not object_storage_enabled():
        return []
    storage = _storage()
    objs = storage.list_objects(f"{PREFIX}/")
    task_ids = []
    for o in objs:
        key = o["key"]
        # Filtra solo i meta.json per dedup (evita doppio-conteggio zip+meta
        if key.endswith(META_SUFFIX):
            # Estrai task_id: "imdf-bundles/task-abc.meta.json" -> "task-abc"
            basename = key[len(PREFIX) + 1:]  # strip "imdf-bundles/"
            task_id = basename[:-len(META_SUFFIX)]
            task_ids.append(task_id)
    return task_ids


def delete_bundle(task_id: str, *, cleanup_local: bool = True) -> int:
    """Cancella tutti gli oggetti del bundle da storage. Ritorna count
    oggetti eliminati.

    IMDF-3: storage layout e' per-file. Eliminiamo:
      - meta.json (``{task_id}.meta.json``)
      - tutti i file individuali sotto ``{task_id}/files/*``
      - ``_gdb.zip`` se presente
      - legacy ``{task_id}.zip`` se presente (compat con bundle vecchi)
    """
    storage = _storage()
    deleted = 0

    # Meta + legacy zip + gdb zip: key fissi.
    for key in (_meta_key(task_id), _zip_key(task_id), _gdb_zip_key(task_id)):
        try:
            storage.delete(key)
            deleted += 1
        except Exception as e:
            logger.warning(f"delete {key} fallito (non-fatal): {e}")

    # File individuali: list + delete.
    try:
        objs = storage.list_objects(_files_prefix(task_id) + "/")
    except Exception as e:
        logger.warning(f"list files for delete {task_id} fallito: {e}")
        objs = []
    for o in objs:
        key = o.get("key")
        if not key:
            continue
        try:
            storage.delete(key)
            deleted += 1
        except Exception as e:
            logger.warning(f"delete {key} fallito (non-fatal): {e}")

    # Cleanup cache locale sul nodo corrente (se presente
    if cleanup_local:
        local = _local_dir(task_id)
        if local.exists():
            rmtree_safe(local, logger=logger)

    return deleted
