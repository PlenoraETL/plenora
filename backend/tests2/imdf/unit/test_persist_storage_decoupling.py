"""
Contract test — persist <-> storage architecture (IMDF-3 RAM-first).

IMDF-3 ha INVERTITO la dependence direction precedente. Storico:
  - Pre-IMDF-3 (d3_imdf_boundaries.md §3.4 + §4.2 + §8 Opzione D):
    `persist.py` = local materializer puro (no storage import); il
    worker orchestrava `persist + storage.save_bundle` separatamente.
  - IMDF-3: persist diventa l'orchestratore RAM-first verso storage
    canonico. `persist_imdf_bundle(bundle, ...)` carica
    `bundle.files` su object storage come oggetti individuali +
    .gdb zip + meta. Il worker chiama SOLO `persist_imdf_bundle`.

Razionale dell'inversione:
  - Post-bundle RAM-first (IMDF-2 `files: dict[str, bytes]`), tenere
    persist staccato da storage forzava il worker a fare un giro
    inutile su disco (`local_dir` come canonical state) prima di
    poter chiamare `save_bundle(local_dir)`. Con bytes-first, il
    canonical state e' bundle.files; persist e' il punto naturale
    per la fan-out su storage.

I test in questo file pinnano il NUOVO invariant:
  - `persist.py` PUO' importare storage (era proibito).
  - `persist_imdf_bundle` usa `save_bundle_files` (RAM-first),
    NON `save_bundle` (legacy zip-based).
  - Il worker chiama `persist_imdf_bundle`, NON `save_bundle`
    direttamente.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


def _fake_bundle_in_memory(site_id: str = "LO2244"):
    """ImdfBundle in-memory (post IMDF-2 shape RAM-first), pronto per
    essere consumato da `persist_imdf_bundle`. `tmp_dir=None` perche'
    non ci sono componenti path-required (.gdb/.ifc) in questo fake."""
    from app.core.imdf.loader import ImdfBundle

    return ImdfBundle(
        tmp_dir=None,
        site_id=site_id,
        files={
            "manifest.json": json.dumps({"sites": {site_id: {}}}).encode("utf-8"),
            "validation.json": json.dumps(
                {"passed": True, "total_errors": 0, "total_warnings": 0}
            ).encode("utf-8"),
        },
    )


def test_persist_imdf_bundle_uploads_via_save_bundle_files(monkeypatch):
    """IMDF-3: `persist_imdf_bundle` invoca `save_bundle_files`
    (RAM-first, individual upload) NON `save_bundle` (legacy zip)."""
    from app.core.imdf import persist as persist_mod
    from app.core.imdf import storage as storage_mod

    save_bundle_files_mock = MagicMock(
        return_value={"file_keys": [], "meta_key": "fake-meta", "gdb_zip_key": None}
    )
    monkeypatch.setattr(storage_mod, "save_bundle_files", save_bundle_files_mock)
    # `storage.save_bundle` legacy tombstoned (IMDF-4 step 4). Sentinel
    # via getattr per evitare import failure se anche il monkeypatch lo
    # cercasse.
    assert not hasattr(storage_mod, "save_bundle"), (
        "save_bundle legacy tombstoned post-IMDF-4 step 4."
    )

    bundle = _fake_bundle_in_memory()
    result = persist_mod.persist_imdf_bundle(
        bundle, task_id="task-imdf3", project_id="proj-1",
    )

    # Happy path preservato.
    assert "storage_keys" in result and "meta" in result
    assert result["meta"]["site_id"] == "LO2244"

    # save_bundle_files chiamato esattamente 1 volta.
    assert save_bundle_files_mock.call_count == 1, (
        "persist_imdf_bundle deve invocare save_bundle_files una volta sola"
    )
    call_kwargs = save_bundle_files_mock.call_args.kwargs
    call_args = save_bundle_files_mock.call_args.args
    # task_id come primo arg positional; files dict come secondo.
    assert call_args[0] == "task-imdf3"
    assert "manifest.json" in call_args[1]
    assert "validation.json" in call_args[1]

    # save_bundle legacy gia' tombstoned (assert sopra). Nessun ulteriore
    # check necessario.


def test_persist_imdf_bundle_propagates_validation_summary_from_bytes():
    """IMDF-3: validation_summary letto da `bundle.files['validation.json']`
    senza filesystem read. Pin del read-from-bytes path."""
    from app.core.imdf import persist as persist_mod
    from app.core.imdf import storage as storage_mod

    bundle = _fake_bundle_in_memory()
    bundle.files["validation.json"] = json.dumps(
        {"passed": False, "total_errors": 7, "total_warnings": 3}
    ).encode("utf-8")

    captured_meta = {}

    def _capture(task_id, files, meta, **kw):
        captured_meta.update(meta)
        return {"file_keys": [], "meta_key": "k", "gdb_zip_key": None}

    storage_mod.save_bundle_files = _capture  # monkeypatch diretto
    try:
        persist_mod.persist_imdf_bundle(
            bundle, task_id="t", project_id="p",
        )
    finally:
        # Re-import per ripristinare (non c'e' monkeypatch fixture qui).
        import importlib
        importlib.reload(storage_mod)

    assert captured_meta.get("validation_summary") == {
        "passed": False,
        "total_errors": 7,
        "total_warnings": 3,
    }


def test_persist_imdf_bundle_counts_features_from_files_dict(monkeypatch):
    """IMDF-3: building_count / level_count / unit_count derivati dai
    geojson in `bundle.files`, niente filesystem read."""
    from app.core.imdf import persist as persist_mod
    from app.core.imdf import storage as storage_mod

    bundle = _fake_bundle_in_memory(site_id="S99")
    bundle.files["S99/building.geojson"] = json.dumps(
        {"type": "FeatureCollection", "features": [{}, {}, {}]}
    ).encode("utf-8")
    bundle.files["S99/level.geojson"] = json.dumps(
        {"type": "FeatureCollection", "features": [{}, {}]}
    ).encode("utf-8")

    captured_meta = {}

    def _capture(task_id, files, meta, **kw):
        captured_meta.update(meta)
        return {"file_keys": [], "meta_key": "k", "gdb_zip_key": None}

    monkeypatch.setattr(storage_mod, "save_bundle_files", _capture)

    persist_mod.persist_imdf_bundle(
        bundle, task_id="t", project_id="p",
    )

    assert captured_meta.get("building_count") == 3
    assert captured_meta.get("level_count") == 2
    # unit_count assente perche' bundle.files non ha unit.geojson.
    assert "unit_count" not in captured_meta


def test_enrichment_task_imdf_branch_calls_persist_imdf_bundle():
    """IMDF-3: il worker IMDF branch usa il single-call
    `persist_imdf_bundle`. NON deve chiamare piu' separatamente
    `persist_imdf_bundle_to_disk` + `save_bundle`."""
    from workers.tasks import enrichment_task

    code = getattr(
        enrichment_task.execute_enrichment_task,
        "__wrapped__",
        enrichment_task.execute_enrichment_task,
    ).__code__

    def _walk(co, target):
        if target in co.co_names:
            return True
        for const in co.co_consts:
            if hasattr(const, "co_names") and _walk(const, target):
                return True
        return False

    assert _walk(code, "persist_imdf_bundle"), (
        "Worker deve riferire `persist_imdf_bundle` (single-call IMDF-3) "
        "come callee del branch IMDF."
    )
    assert not _walk(code, "save_bundle"), (
        "Worker non deve piu' chiamare `save_bundle` direttamente. "
        "L'upload e' orchestrato da `persist_imdf_bundle` (IMDF-3 "
        "reversal della precedente decoupling)."
    )
