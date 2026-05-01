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
    """Regression guard: il modulo `app.core.imdf.persist` non deve avere
    `app.core.imdf.storage` nel namespace (re-import residuo).

    Lo spostamento della chiamata nel worker e' utile solo se il coupling
    viene rimosso anche a livello di import. Un import residuo segnala
    che la call site non e' stata davvero rimossa.

    Replacement R-04b di `inspect.getsource(persist_mod) + "from app.core.imdf import storage" in source`:
    introspect del namespace modulo + sys.modules. Behavior-bound:
    cattura il fatto che il symbol `storage` sia stato bound nel
    namespace di persist_mod, indipendente dalla forma sintattica
    dell'import (from / as / re-export).
    """
    import sys
    from app.core.imdf import persist as persist_mod

    # 1. Il namespace modulo non deve esporre `storage` o un suo simbolo top-level.
    forbidden_attrs = ("storage", "save_bundle", "_storage")
    for attr in forbidden_attrs:
        bound = getattr(persist_mod, attr, None)
        if bound is not None:
            # Verifica che non sia il modulo storage.
            mod_name = getattr(bound, "__module__", "") or ""
            obj_name = getattr(bound, "__name__", "") or ""
            assert "imdf.storage" not in mod_name, (
                f"Regression D3 commit 4: `{attr}` in persist_mod e' "
                f"bound a {mod_name} (storage). Persist deve restare local "
                f"materializer puro (non conosce S3/object_storage). "
                f"L'orchestrazione vive nel worker (enrichment_task)."
            )
            assert "imdf.storage" not in obj_name, (
                f"Regression D3 commit 4: `{attr}` in persist_mod referenzia "
                f"{obj_name} (storage)."
            )

    # 2. Il bytecode di persist_mod non deve avere `storage` come name access.
    #    (cattura `storage.save_bundle()` anche se importato lazy nel body.)
    code = persist_mod.__dict__.get("__code__") or getattr(
        persist_mod, "__loader__", None,
    )
    # Ispeziona ogni callable top-level del modulo.
    for attr_name in dir(persist_mod):
        attr = getattr(persist_mod, attr_name, None)
        if not hasattr(attr, "__code__"):
            continue
        co = attr.__code__

        def _walk(co):
            for n in co.co_names:
                if n == "save_bundle":
                    return True
            for const in co.co_consts:
                if hasattr(const, "co_names") and _walk(const):
                    return True
            return False

        assert not _walk(co), (
            f"Regression D3 commit 4: persist_mod.{attr_name} fa "
            f"riferimento a `save_bundle` nel bytecode. Persist deve "
            f"restare local materializer puro."
        )


# ── 3. Static: enrichment_task invoca storage.save_bundle nel branch IMDF ─


def test_enrichment_task_imdf_branch_calls_storage_save_after_persist():
    """Regression guard: il worker deve orchestrare persist -> storage nel
    branch IMDF.

    R-04b mix:
      - callee ref via bytecode probe (refactor-resilient).
      - warning text intenzionalmente verbatim (ops grep contract:
        alert/dashboard fanno grep su quella stringa esatta — il
        wording e' un contract pubblico verso ops, non source-bound
        fragile).
    """
    from workers.tasks import enrichment_task

    # Bytecode probe per i callee identifier (refactor-resilient).
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

    assert _walk(code, "save_bundle"), (
        "Regression D3 commit 4: enrichment_task non riferisce "
        "`save_bundle` come callee. Il worker e' l'orchestratore "
        "post-persist (R2); rimuoverlo da qui spezza il multi-host "
        "senza readd in persist."
    )
    assert _walk(code, "persist_imdf_bundle_to_disk"), (
        "Regression D3 commit 4: enrichment_task non riferisce "
        "`persist_imdf_bundle_to_disk`."
    )

    # Warning text: contract verbatim verso ops (grep su alert/dashboard).
    # Restiamo source-bound qui di proposito — la stringa e' un contract
    # pubblico, non un dettaglio implementativo. Source dell'intero modulo
    # garantisce di catturarla anche se vive in funzioni nested.
    source = inspect.getsource(enrichment_task)
    assert "imdf storage upload fallito (non-fatal" in source, (
        "D3 commit 4: il wording del logger.warning deve restare verbatim "
        "('imdf storage upload fallito (non-fatal, bundle disponibile solo "
        "sul nodo corrente)') per non rompere alert/dashboard che ne "
        "fanno grep."
    )
