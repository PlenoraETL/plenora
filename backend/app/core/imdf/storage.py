"""
Storage IMDF bundle su object storage cloud-agnostic.

Chiude R2 del multi-host audit: i bundle IMDF non vivono piu' solo su
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, TypedDict

from app.config import settings as _settings
from app.core.io.object_storage import object_storage_enabled
from app.core.io.local_cleanup import rmtree_safe
from app.core.resources.bounded_reader import read_stream_bounded
from app.core.resources.exceptions import MemoryLimitExceededError
from app.core.resources.resource_caps import resolve_resource_max_bytes
from app.shared.contracts.payloads import JsonObject, StringMap
from app.shared.resources.storage_layout import IMDF_BUNDLES_PREFIX
from app.utils.logger import get_logger


logger = get_logger(__name__)


# Module-level alias kept for internal call sites and tests.
PREFIX = IMDF_BUNDLES_PREFIX
META_SUFFIX = ".meta.json"
BundleFiles = dict[str, bytes]


class StorageObjectStream(TypedDict, total=False):
    """Shape ritornata da `*.get_object_stream(key)` (cloud backend).

    Vedi `app.infrastructure.storage.cloud_backends`: `body` e' un iterable
    di chunk bytes (streaming-friendly), `size` i bytes totali, `content_type`
    il MIME. `total=False` perche' i consumer leggono solo `body`."""

    body: Iterable[bytes]
    size: int
    content_type: str


def _storage():
    """Lazy load della factory cloud runtime.

    Solleva `ObjectStorageDisabledError` se la capability globale e' off:
    i caller read/list degradano graceful catturando l'eccezione;
    save_bundle invece deve fallire perche' l'upload deve completare."""
    from app.core.io.object_storage import get_runtime_object_storage
    return get_runtime_object_storage()


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


def _read_storage_stream_bytes(
    stream: StorageObjectStream,
    *,
    key: str,
    stage: str,
) -> bytes:
    label = key.rsplit("/", 1)[-1] or key
    return read_stream_bounded(
        stream.get("body", ()),
        max_bytes=resolve_resource_max_bytes(),
        label=label,
        stage=stage,
    )


def _local_dir(task_id: str) -> Path:
    return _settings.IMDF_BUNDLES_DIR / task_id




def save_bundle_files(
    task_id: str,
    files: BundleFiles,
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
    direttamente da storage. Ritorna ``None`` se l'oggetto non esiste
    su storage o la capability object_storage e' off.
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
    return _read_storage_stream_bytes(
        stream,
        key=key,
        stage="imdf_bundle_file_read",
    )


# IMDF-4: storage-first JSON reader helpers
# Wrappano `read_bundle_file_bytes` decodificando i bytes come JSON. Pensati
# per i reader HTTP direct-from-storage senza il giro
# `ensure_local_bundle + Path.read_text`.
# I consumer che richiedono .gdb usano `open_gdb_in_vsimem`.


def _read_bundle_json(task_id: str, name: str) -> Optional[JsonObject]:
    raw = read_bundle_file_bytes(task_id, name)
    if raw is None:
        return None
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception as e:
        logger.warning(f"[{task_id}] read_bundle_json {name!r} decode failed: {e}")
        return None
    return obj if isinstance(obj, dict) else None


def read_manifest(task_id: str) -> Optional[JsonObject]:
    """Storage-first read del ``manifest.json`` del bundle."""
    return _read_bundle_json(task_id, "manifest.json")


def read_validation(task_id: str) -> Optional[JsonObject]:
    """Storage-first read del ``validation.json`` del bundle."""
    return _read_bundle_json(task_id, "validation.json")


def read_renderers(task_id: str) -> Optional[JsonObject]:
    """Storage-first read del ``renderers.json`` del bundle."""
    return _read_bundle_json(task_id, "renderers.json")


def read_geojson(task_id: str, relative_name: str) -> Optional[JsonObject]:
    """Storage-first read di un GeoJSON del bundle.

    ``relative_name`` e' il path interno, e.g. ``{site_id}/building.geojson``
    o ``{site_id}/unit.geojson``.
    """
    if not relative_name.endswith(".geojson"):
        # Defensive: gli IMDF GeoJSON hanno sempre estensione esplicita.
        return None
    return _read_bundle_json(task_id, relative_name)


@contextmanager
def open_gdb_in_vsimem(task_id: str, site_id: str) -> Iterator[Optional[str]]:
    """Idrata il `.gdb` di un bundle in GDAL `/vsimem` (zero disk).

    IMDF-5: sostituisce `hydrate_gdb_to_temp`. Il `.gdb.zip` viene
    scaricato da storage in RAM e registrato in `/vsimem` via
    l'adapter ctypes `app.core.io.vsimem.vsi_register_bytes`.
    Pyogrio legge il `.gdb` interno via path `/vsizip//vsimem/.../
    {site_id}.gdb` senza materializzare niente su disco.

    Layout su storage (post-IMDF-3): ``{PREFIX}/{task_id}/_gdb.zip``.
    Lo zip contiene un singolo top-level `{site_id}.gdb/` directory.

    Args:
        task_id: id del bundle.
        site_id: nome della `.gdb` dir interna (e.g. ``"SITE_X"``),
            tipicamente ottenuto da ``get_meta_only(task_id)["site_id"]``.

    Yields:
        Path GDAL VSI ``/vsizip//vsimem/plenora_imdf/{task_id}/
        _gdb.zip/{site_id}.gdb`` pronto per `pyogrio.list_layers` /
        `read_dataframe`. ``None`` se il bundle non ha `.gdb` su
        storage (caller decide come trattare: BUNDLE_MISSING /
        GDB_MISSING / etc).

    Raises:
        Propaga errori storage del download; errori VSI ctypes.
    """
    if not object_storage_enabled():
        yield None
        return
    storage = _storage()
    gdb_key = _gdb_zip_key(task_id)

    # Probe esistenza via list_objects (evita exception on missing key).
    try:
        objs = storage.list_objects(gdb_key)
        has_gdb = any(o.get("key") == gdb_key for o in objs)
    except Exception as e:
        logger.debug(f"[{task_id}] open_gdb_in_vsimem probe failed: {e}")
        yield None
        return
    if not has_gdb:
        yield None
        return

    stream = storage.get_object_stream(gdb_key)
    gdb_bytes = _read_storage_stream_bytes(
        stream,
        key=gdb_key,
        stage="imdf_gdb_zip_vsimem",
    )

    # Import locale per evitare ctypes load all'import-time del modulo
    # (tests fuori container non hanno libgdal).
    from app.core.io.vsimem import vsi_register_bytes

    vsi_zip_path = f"/vsimem/plenora_imdf/{task_id}/_gdb.zip"
    pyogrio_path = f"/vsizip/{vsi_zip_path}/{site_id}.gdb"

    with vsi_register_bytes(vsi_zip_path, gdb_bytes):
        logger.info(
            f"[{task_id}] .gdb registrato in /vsimem ({len(gdb_bytes)} bytes), "
            f"path={pyogrio_path}"
        )
        yield pyogrio_path


def list_bundle_file_entries(task_id: str) -> List[JsonObject]:
    """Lista i file individuali di un bundle su storage (IMDF-4).

    Ritorna una list di dict ``{"path": relative_name, "size": int}``
    derivata da ``list_objects({PREFIX}/{task_id}/files/)``. ``path`` e'
    relativo (strip del prefix `files/`) per matchare il layout usato
    dal frontend (i.e. ``{site_id}/foo.geojson``).
    Lista vuota se la capability object_storage e' off o nessun file
    individuale presente.
    """
    if not object_storage_enabled():
        return []
    storage = _storage()
    prefix = _files_prefix(task_id) + "/"
    try:
        objs = storage.list_objects(prefix)
    except Exception as e:
        logger.debug(f"[{task_id}] list_bundle_file_entries failed: {e}")
        return []
    out: List[JsonObject] = []
    for o in objs:
        key = o.get("key")
        if not key or not key.startswith(prefix):
            continue
        rel = key[len(prefix):]
        if not rel:
            continue
        out.append({"path": rel, "size": int(o.get("size") or 0)})
    return out


# Tombstone (IMDF-4 step 4): legacy `save_bundle(task_id, local_dir,
# meta)` rimosso. Era il writer zip-based pre-IMDF-3 (zippa local_dir +
# upload). Zero caller in produzione. La writer entry-point canonica e'
# `save_bundle_files` (IMDF-3). Tentativi di chiamarla post-tombstone
# falliscono con AttributeError.


# Read (endpoint side


# Tombstone (QUERY-1 step 3): `ensure_local_bundle` rimosso. Gli
# endpoint IMDF leggono direttamente da storage:
#   - GET metadata + list: `imdf.storage.read_*`
#   - publish/validate: `open_gdb_in_vsimem` -> `/vsizip//vsimem/`
#   - DELETE: `delete_bundle` su storage canonical


def get_meta_only(task_id: str) -> Optional[JsonObject]:
    """Legge SOLO `plenora_metadata.json` da S3 (senza download zip).

    Usato dal list endpoint per enumerare bundle senza costi di estrazione.
    Ritorna None se il meta non esiste su S3 (bundle non ancora pubblicato
    o solo local-cached) oppure se la capability object storage e' off.
    """
    if not object_storage_enabled():
        return None
    mk = _meta_key(task_id)
    try:
        storage = _storage()
        stream = storage.get_object_stream(mk)
        body_bytes = _read_storage_stream_bytes(
            stream,
            key=mk,
            stage="imdf_bundle_meta_read",
        )
        return json.loads(body_bytes.decode("utf-8"))
    except MemoryLimitExceededError:
        raise
    except Exception as e:
        logger.debug(f"[{task_id}] meta only read skipped: {e}")
        return None


def list_task_ids() -> List[str]:
    """Elenca task_id dei bundle presenti su S3 (via meta.json).

    Usato dal list endpoint per vedere bundle da tutti i nodi (non solo
    quelli cached sul nodo corrente). ritorna [] se la
    capability object storage e' disabilitata."""
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
    """
    storage = _storage()
    deleted = 0

    # Meta + gdb zip: key fissi.
    for key in (_meta_key(task_id), _gdb_zip_key(task_id)):
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

    # Cleanup directory locale sul nodo corrente (se presente).
    if cleanup_local:
        local = _local_dir(task_id)
        if local.exists():
            rmtree_safe(local, logger=logger)

    return deleted
