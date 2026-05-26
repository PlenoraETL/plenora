"""
Contract test — validate-publish hydrate cross-node (TOMBSTONED).

Storia:
- Pre-IMDF-3: `/api/imdf/validate-publish` chiamava
  `_hydrate_local_cache` per scaricare il bundle da S3 e leggere
  meta/gdb su filesystem locale prima del check ACL.
- IMDF-4 step 3: ACL via `assert_bundle_group_access_from_meta`
  + `imdf.storage.get_meta_only` (storage-first, no hydrate).
- IMDF-5: GDB via `open_gdb_in_vsimem` (RAM-only via /vsimem ctypes).
- QUERY-1 step 3: `ensure_local_bundle` + `_hydrate_local_cache`
  rimossi definitivamente. Endpoint validate-publish e' fully
  storage-first.

Le invarianti cross-node + cross-tenant restano coperte da:
  - `tests2/imdf/unit/test_router_bundle_metadata_storage_first.py`
    (`test_get_bundle_metadata_forbids_cross_tenant_via_meta_acl`)
  - `tests2/imdf/unit/test_imdf_storage_open_gdb_vsimem.py`

Questo file resta come sentinel anti-regression sul tombstone.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_validate_publish_hydrate_legacy_tombstoned():
    """Sentinel: `_hydrate_local_cache` rimosso dal router IMDF.
    Validate-publish e' fully storage-first."""
    from app.core.imdf import router as router_mod
    assert not hasattr(router_mod, "_hydrate_local_cache"), (
        "_hydrate_local_cache rimosso post-QUERY-1 step 3. "
        "Validate-publish usa `assert_bundle_group_access_from_meta` "
        "+ `imdf.storage.get_meta_only` storage-first."
    )


def test_storage_ensure_local_bundle_tombstoned():
    """Sentinel: `imdf.storage.ensure_local_bundle` rimosso."""
    from app.core.imdf import storage as storage_mod
    assert not hasattr(storage_mod, "ensure_local_bundle")


def test_bundle_access_hydrate_local_cache_tombstoned():
    """Sentinel: `bundle_access.hydrate_local_cache` rimosso."""
    from app.core.imdf import bundle_access
    assert not hasattr(bundle_access, "hydrate_local_cache")
