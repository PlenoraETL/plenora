"""
Contract test — IMDF site_id single-source (loader autoritativo).

Storia (d3_imdf_boundaries.md §3.1 + §4.1 + R3 + R4):
- Pre-IMDF-3 il `site_id` veniva derivato in 2 posti diversi (loader
  + router) creando drift potenziale.
- IMDF-3 ha unificato la fonte: `loader.ImdfBundle.site_id` e' il
  source-of-truth, scritto da `persist_imdf_bundle` su `meta.site_id`
  (storage), letto dal router via `get_meta_only`.

Post QUERY-1 step 3: `persist_imdf_bundle_to_disk` + `ensure_local_bundle`
rimossi. I test originali patchavano questi simboli; ora il path
canonical e' `persist_imdf_bundle` + `get_meta_only`, coperti da:
  - `tests2/imdf/unit/test_router_bundle_metadata_storage_first.py`
    (test_get_bundle_metadata_uses_storage_readers_no_local_hydration)
  - `tests2/imdf/unit/test_persist_storage_decoupling.py`

Questo file resta come sentinel anti-regression sull'invariant +
sui tombstone.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_site_id_lives_in_imdf_bundle_only():
    """`site_id` e' field di `ImdfBundle` (loader). Router non lo
    re-deriva: lo legge dal meta storage."""
    from app.core.imdf.loader import ImdfBundle
    import dataclasses
    fields = {f.name for f in dataclasses.fields(ImdfBundle)}
    assert "site_id" in fields, (
        "ImdfBundle.site_id deve restare come field (single source-of-truth)."
    )


def test_router_uses_storage_meta_for_site_id():
    """Source-level pin: il GET /bundles/{id} estrae `site_id` dal
    `meta` letto via `get_meta_only`, NON da scansione filesystem."""
    from pathlib import Path
    backend_root = Path(__file__).resolve().parents[3]
    source = (
        backend_root / "app" / "core" / "imdf" / "router.py"
    ).read_text(encoding="utf-8")
    assert "meta.get(\"site_id\")" in source, (
        "Router deve leggere site_id dal meta storage."
    )


def test_legacy_persist_to_disk_tombstoned():
    """Sentinel: `persist_imdf_bundle_to_disk` rimosso (era usato dai
    test pre-IMDF-3 per testare site_id resolution su filesystem)."""
    from app.core.imdf import persist as persist_mod
    assert not hasattr(persist_mod, "persist_imdf_bundle_to_disk")


def test_legacy_ensure_local_bundle_tombstoned():
    """Sentinel: `ensure_local_bundle` rimosso. Site_id NON viene
    piu' derivato da scan filesystem; viene letto dal meta storage."""
    from app.core.imdf import storage as storage_mod
    assert not hasattr(storage_mod, "ensure_local_bundle")
