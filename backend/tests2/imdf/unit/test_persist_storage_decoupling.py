"""Contract test D3 commit 4 — persist.py non chiama storage.

Inversione di dep rimossa (d3_imdf_boundaries.md §3.4 + §4.2 + §8 Opzione D,
R2 ratificata 2026-04-22):
- persist.py = local materializer puro, non conosce S3 ne' object storage.
- storage.py = cross-node backup invariato.
- enrichment_task (worker) = orchestrator esplicito: chiama persist e poi
  storage.save_bundle, con error policy (best-effort logger.warning) decisa
  nel worker invece che dentro persist.

Pre-commit: persist.py:147-159 chiamava inline _imdf_storage.save_bundle
con try/except. Post-commit quelle righe sono rimosse; il worker invoca
save_bundle esplicitamente dopo persist.
"""
from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.utils.logger  # noqa: F401
import app.core.imdf.loader  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# ── Helpers ─────────────────────────────────────────────────────────────


def _fake_bundle(tmp_path: Path, site_id: str = "LO2244"):
    """Finto ImdfBundle con struttura minima (pattern gia' usato in
    test_imdf_persist_bundle_contract.py)."""
    from app.core.imdf.loader import ImdfBundle

    src_tmp = tmp_path / f"imdf_{site_id}"
    src_tmp.mkdir()
    gdb = src_tmp / f"{site_id}.gdb"
    gdb.mkdir()
    (gdb / "a00000001.gdbtable").write_bytes(b"\x00fake gdb\x00")
    site_dir = src_tmp / site_id
    site_dir.mkdir()
    (site_dir / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    (src_tmp / "manifest.json").write_text(json.dumps({"version": "1.0"}), encoding="utf-8")
    (src_tmp / "validation.json").write_text(
        json.dumps({"passed": True, "total_errors": 0, "total_warnings": 0}),
        encoding="utf-8",
    )
    return ImdfBundle(
        tmp_dir=src_tmp,
        site_id=site_id,
        gdb_path=gdb,
        manifest_path=src_tmp / "manifest.json",
        validation_path=src_tmp / "validation.json",
    )


@pytest.fixture
def fake_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import app.config as config_mod
    d = tmp_path / "imdf_bundles"
    d.mkdir()
    settings = SimpleNamespace(IMDF_BUNDLES_DIR=d, IMDF_BUNDLE_TTL_HOURS=48)
    monkeypatch.setattr(config_mod, "settings", settings)
    return d


# ── 1. Functional: persist NON chiama storage.save_bundle ───────────────


def test_persist_does_not_call_storage_save_bundle(tmp_path, fake_settings, monkeypatch):
    """Runtime check: eseguendo `persist_imdf_bundle_to_disk` con un bundle
    valido, la funzione non invoca piu' `storage.save_bundle` (R2).

    Pre-commit 4: save_bundle era chiamato inline (persist.py:147-159).
    Post-commit: il worker orchestra; persist resta local materializer puro.
    """
    from app.core.imdf.persist import persist_imdf_bundle_to_disk
    import app.core.imdf.storage as storage_mod

    save_bundle_mock = MagicMock()
    monkeypatch.setattr(storage_mod, "save_bundle", save_bundle_mock)

    bundle = _fake_bundle(tmp_path)
    result = persist_imdf_bundle_to_disk(
        bundle, task_id="task-decoupling", project_id="proj-1",
    )

    # persist ha completato correttamente (happy path preservato)
    assert "path" in result and "meta" in result
    assert result["meta"]["site_id"] == "LO2244"

    # storage.save_bundle NON deve essere stato chiamato da persist.
    assert save_bundle_mock.call_count == 0, (
        "Regression D3 commit 4: persist_imdf_bundle_to_disk ha chiamato "
        "storage.save_bundle. L'inversione di dependenza e' stata "
        "re-introdotta. L'orchestrazione deve vivere nel worker "
        "(enrichment_task), non in persist."
    )


# ── 2. Static: persist source non importa storage ──────────────────────


def test_persist_module_does_not_import_storage():
    """Regression guard: il modulo `app.core.imdf.persist` non deve contenere
    `import app.core.imdf.storage` ne' `from app.core.imdf import storage`.

    Lo spostamento della chiamata nel worker e' utile solo se il coupling
    viene rimosso anche a livello di import. Un import residuo segnalerebbe
    che la call site non e' stata davvero rimossa.
    """
    from app.core.imdf import persist as persist_mod

    source = inspect.getsource(persist_mod)

    forbidden_imports = (
        "from app.core.imdf import storage",
        "import app.core.imdf.storage",
        "from app.core.imdf.storage",
    )
    for imp in forbidden_imports:
        assert imp not in source, (
            f"Regression D3 commit 4: `{imp}` re-introdotto in "
            f"app/core/imdf/persist.py. Persist deve restare local "
            f"materializer puro (non conosce S3/object_storage). "
            f"L'orchestrazione vive nel worker (enrichment_task)."
        )


# ── 3. Static: enrichment_task invoca storage.save_bundle nel branch IMDF ─


def test_enrichment_task_imdf_branch_calls_storage_save_after_persist():
    """Regression guard: il worker deve orchestrare persist -> storage nel
    branch IMDF. Se qualcuno rimuove la chiamata a save_bundle dal worker
    senza re-introdurla in persist, i bundle non saranno piu' replicati
    cross-node (regressione silenziosa che spezzerebbe multi-host).

    Test statico via inspect.getsource (pattern `test_enrichment_task_
    tenant_binding_lock.py:269`).
    """
    from workers.tasks import enrichment_task

    source = inspect.getsource(enrichment_task)

    # Deve esserci la chiamata a save_bundle nel worker.
    assert "_imdf_storage.save_bundle" in source or ".save_bundle(" in source, (
        "Regression D3 commit 4: la chiamata a `storage.save_bundle` non "
        "risulta piu' presente in workers/tasks/enrichment_task.py. Il "
        "worker e' l'orchestratore post-persist (R2); rimuoverlo da qui "
        "spezza il multi-host senza readd in persist."
    )

    # Deve essere dopo persist_imdf_bundle_to_disk nel source.
    persist_idx = source.find("persist_imdf_bundle_to_disk(")
    save_idx = source.find(".save_bundle(")
    assert persist_idx != -1, "persist_imdf_bundle_to_disk non trovato"
    assert save_idx != -1, "save_bundle non trovato"
    assert save_idx > persist_idx, (
        "D3 commit 4: save_bundle deve apparire DOPO "
        "persist_imdf_bundle_to_disk nel source. L'ordine persist -> storage "
        "garantisce cleanup tmp_dir (finally di persist) prima del push S3."
    )

    # Il warning deve essere preservato verbatim (grep operativi).
    assert "imdf storage upload fallito (non-fatal" in source, (
        "D3 commit 4: il wording del logger.warning deve restare verbatim "
        "('imdf storage upload fallito (non-fatal, bundle disponibile solo "
        "sul nodo corrente)') per non rompere alert/dashboard che ne "
        "fanno grep."
    )
