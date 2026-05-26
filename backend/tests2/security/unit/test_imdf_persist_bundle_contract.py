"""
Contract test — `persist_imdf_bundle_to_disk` (TOMBSTONED post QUERY-1 step 3).

Storia:
- Pre-IMDF-3: `persist_imdf_bundle_to_disk` era il canonical local
  materializer (`IMDF_BUNDLES_DIR/{task_id}/` con `plenora_metadata.json`).
- IMDF-3 (commit `58859082`): introdotto `persist_imdf_bundle` come
  storage-direct (S3 + meta key); `persist_imdf_bundle_to_disk` legacy
  preservato per test/script.
- QUERY-1 step 3: `persist_imdf_bundle_to_disk` rimosso. Tutti i caller
  (script `imdf_real_e2e_check.py` + worker IMDF + test) migrati a
  `persist_imdf_bundle`.

I 5 test originali coprivano:
  - happy path move + metadata
  - cleanup tmp dir su move error
  - replace destinazione esistente
  - validation_summary in meta
  - layer counts da geojson on-disk

Tutte queste invarianti restano valide per `persist_imdf_bundle` ma
con shape diversa (meta scritto su storage object, non su filesystem).
Lo storage-direct e' gia' coperto da
`tests2/imdf/unit/test_persist_storage_decoupling.py`.

Questo file resta come sentinel anti-regression sul tombstone.
"""
from __future__ import annotations

import pytest


pytestmark = [
    pytest.mark.unit, pytest.mark.security,
    pytest.mark.contract, pytest.mark.regression,
]


def test_persist_imdf_bundle_to_disk_tombstoned():
    """Sentinel: `persist_imdf_bundle_to_disk` rimosso dal modulo.
    Anti-regression sul tombstone QUERY-1 step 3."""
    from app.core.imdf import persist as persist_mod
    assert not hasattr(persist_mod, "persist_imdf_bundle_to_disk"), (
        "persist_imdf_bundle_to_disk deve essere tombstoned post-QUERY-1 "
        "step 3. Lo storage-direct e' `persist_imdf_bundle(bundle, "
        "task_id, project_id)`."
    )


def test_persist_imdf_bundle_storage_direct_present():
    """Sentinel: la sostituizione `persist_imdf_bundle` (storage-direct)
    e' presente come unica entry."""
    from app.core.imdf.persist import persist_imdf_bundle
    import inspect
    sig = inspect.signature(persist_imdf_bundle)
    assert "bundle" in sig.parameters
    assert "task_id" in sig.parameters
    assert "project_id" in sig.parameters
    assert "dest_root" not in sig.parameters, (
        "persist_imdf_bundle NON deve accettare dest_root (era legacy "
        "filesystem-bound). Storage e' canonical."
    )
