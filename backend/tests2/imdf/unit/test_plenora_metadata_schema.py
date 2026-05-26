"""
Contract test — schema `plenora_metadata.json` (post QUERY-1 step 3).

Pre-IMDF-3 `plenora_metadata.json` veniva scritto su filesystem
locale dal legacy `persist_imdf_bundle_to_disk`. Post-IMDF-3 e'
upload'ato su storage come `{task_id}.meta.json` da
`persist_imdf_bundle` -> `imdf.storage.save_bundle_files`.

I 4 test originali coprivano shape del meta dict + `has_gdb/ifc/
renderers` flags + validation_summary + counts geojson. Tutte le
invarianti restano valide ma sono ora coperte da:
  - `tests2/imdf/unit/test_persist_storage_decoupling.py`
    (test_persist_imdf_bundle_propagates_validation_summary,
     test_persist_imdf_bundle_counts_features_from_files_dict).

Questo file resta come sentinel anti-regression sul tombstone.
"""
from __future__ import annotations

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def test_persist_imdf_bundle_to_disk_tombstoned_for_metadata_writes():
    """Sentinel: il meta non e' piu' scritto su filesystem (era
    `output_dir/plenora_metadata.json`). Lo schema vive nel meta
    dict prodotto da `persist_imdf_bundle` + uploaded come
    `{task_id}.meta.json` su storage."""
    from app.core.imdf import persist as persist_mod
    assert not hasattr(persist_mod, "persist_imdf_bundle_to_disk"), (
        "Schema meta scritto su filesystem rimosso post-QUERY-1 step 3. "
        "Vive ora come storage object via persist_imdf_bundle."
    )


def test_persist_imdf_bundle_storage_meta_shape():
    """Sentinel: il meta dict prodotto da `persist_imdf_bundle` ha la
    stessa shape contrattuale (project_id, task_id, site_id,
    has_gdb/ifc/renderers, saved_at). Pin sul layout per non far
    drift il frontend che lo legge."""
    from app.core.imdf.persist import persist_imdf_bundle
    import inspect
    src = inspect.getsource(persist_imdf_bundle)
    for key in (
        "project_id", "task_id", "site_id",
        "has_gdb", "has_ifc", "has_renderers", "saved_at",
    ):
        assert f'"{key}"' in src, (
            f"Meta dict key `{key}` deve essere preservata in "
            f"`persist_imdf_bundle` (contract schema)."
        )
