"""Resource Engine — capability registry, NOT god-engine.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.

This package owns ONE narrow capability: "given a typed resource
ref + a tenant context, return raw bytes / a path / a stream with
size + format metadata, fail-closed on cross-tenant".

Anti-god-engine rules (locked by
`backend/tests2/shared/test_resource_engine_anti_god_engine_lock.py`):

  - NO imports of `pandas`, `geopandas`, `fiona`, `polars`,
    `fastapi`, `app.core.auth`, `app.core.artifacts`,
    `app.core.eseguibili`, `app.core.imdf`, `app.core.intermediate`.
    The engine sits BELOW the domain wrappers; domain wrappers
    call the engine, not the other way.
  - Output is byte-level only: `bytes`, `Path`, `Iterator[bytes]`.
    No `DataFrame`, `GeoDataFrame`, preview dict, parsed records.
  - Tenant binding is mandatory at the type level
    (`ResourceRef.expected_group_id` non-empty by `__post_init__`).
  - One loader per `ResourceKind`. v1 ships two
    (`ResultBlobLoader`, `FileSourceBlobLoader`); other kinds
    (intermediate_artifact, eseguibili_output, imdf_bundle) join
    only after their tenant binding is plumbed through (audit
    sec. 5 + sec. 7 of `BE_RESOURCE_ENGINE_AUDIT.md`).

Public surface
--------------
The four canonical names (asserted by the source-bound lock test):

    ResourceRef        — frozen typed reference (kind + id + tenant)
    Resource           — frozen result envelope (bytes/path/stream + size)
    ResourceLoader     — Protocol per-kind loader implements
    ResourceRegistry   — Protocol dispatcher routes ref -> loader

Usage
-----
::

    from app.core.resources import ResourceRef, default_registry

    ref = ResourceRef(
        kind="result_blob",
        result_id="<uuid>",
        expected_group_id="<group>",
    )
    resource = default_registry().get(ref, mode="bytes")
    blob: bytes = resource.data
"""

from app.core.resources.types import (
    OutputMode,
    Resource,
    ResourceKind,
    ResourceLoader,
    ResourceRef,
    ResourceRegistry,
)
from app.core.resources.registry import default_registry, build_default_registry

__all__ = [
    "OutputMode",
    "Resource",
    "ResourceKind",
    "ResourceLoader",
    "ResourceRef",
    "ResourceRegistry",
    "default_registry",
    "build_default_registry",
]
