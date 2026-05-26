"""Plenora I/O boundary — RAM-first.

Layer orizzontale per tutta la gestione di lettura/scrittura/archiviazione
fuori dal filesystem locale. Vedi refactor_io_storage_ram_only.md per
il piano completo.

Moduli (consolidamento orizzontale 2026-05-26):
  - `policy`: env-driven policy (memory_only / temp_only_explicit / ...)
  - `payloads`: BinaryPayload + StoredObjectRef tipati
  - `materialize`: TOMBSTONE (zero caller post refactor RAM-only)
  - `geospatial`: pyogrio bytes wrapper (read/write GeoJSON/GPKG/FGB/SHP-zip)
  - `vsimem`: ctypes adapter su libgdal per `/vsimem` (ex-`imdf.gdal_vsi`)
  - `zip_guards`: bomb-cap pre-extract + `open_zip_bytes_safe` context
    manager (ex-`utils.zip_safe`)
  - `object_storage`: facade re-export per object storage cloud-agnostic
    (S3/MinIO via boto3) — front door per i caller `app.core.*`
  - `local_filesystem`: data plane boundary per scritture filesystem
    locale (out-of-contract RAM-first ma single audit point) —
    `LocalDataSource` + `startup_validation` passano da qui
  - `local_cleanup`: cleanup primitive per filesystem locale
    (`rmtree_safe` best-effort + `delete_local_file` strict) — usato
    da scheduler, IMDF, eseguibili, results, artifacts, ecc.
    (ex-`app/shared/resources/file_cleanup.py`)

Object storage layering:
  - `app.core.io.object_storage`: facade (entry point del core layer);
    5 symbol re-esportati (get_runtime_object_storage, object_storage_enabled,
    ObjectStorage, ObjectStorageDisabledError, ObjectMeta).
  - `app.infrastructure.storage.cloud_backends`: implementation source-of-truth
    con boto3/MinIO factory, capability check, bucket resolution,
    secrets backend. Simboli specializzati (backup, log_archive, validate
    config, credentials-error formatter) restano deep import.
  - Domain wrapper (3 specializzazioni giustificate):
      - `app.core.results.blob_writer` (result blob + DB row + retention)
      - `app.core.imdf.storage` (bundle multi-file + sidecar meta)
      - `app.core.eseguibili.storage` (single-blob key/value)
    Tutti delegano a `io.object_storage.get_runtime_object_storage()`.

Contract:
  Fuori da questo package (e dai moduli storage autorizzati) NON e'
  ammesso usare tempfile, extractall, Path.write, df export su path,
  FileResponse path-based, shutil.copy, storage upload path-based.
  Il contract test (`tests2/io_boundary/test_no_direct_filesystem_contract.py`)
  blinda la regola contro nuovi usi.
"""

from app.core.io.payloads import BinaryPayload, StoredObjectRef
from app.core.io.policy import (
    DiskWritePolicy,
    get_disk_policy,
    is_persistent_disk_allowed,
    is_temp_disk_allowed,
)

__all__ = [
    "BinaryPayload",
    "DiskWritePolicy",
    "StoredObjectRef",
    "get_disk_policy",
    "is_persistent_disk_allowed",
    "is_temp_disk_allowed",
]
