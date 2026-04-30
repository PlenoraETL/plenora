"""Celery eager harness — test-only.

Phase 1 Test Reliability Batch (PR test/phase-1-test-reliability):

Permette di invocare task Celery sincroni in-process. Niente broker Redis
reale, niente worker, niente DB. Pensato per modernizzare i contract
test source-bound (`inspect.getsource` + grep stringhe) in test
behavior-bound (`task.apply().get()` + spy sui side-effect).

API pubblica:
  - `celery_eager` fixture pytest: flippa `task_always_eager=True` +
    `task_eager_propagates=True` su `celery_app.conf` via monkeypatch
  - `EnrichmentTaskHarness`: composite fixture che istanzia spy +
    monkeypatch per tutte le dipendenze lazy/top-level del task
    `workers.tasks.enrichment_task.execute_enrichment_task`. Costruito
    in modo che l'invocazione end-to-end NON tocchi DB, Redis, FS o
    cloud storage reali.

Vincoli (vedi PR plan):
  - test-only, NON importare da prod
  - nessuna modifica a `backend/workers/tasks/enrichment_task.py`
  - nessuna modifica a `backend/workers/celery_app.py` permanente
  - se il task tenta DB/Redis reali → STOP (test fallisce con errore
    chiaro, no fallback)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock

import pytest


# =============================================================================
# 1. Eager mode fixture
# =============================================================================


@pytest.fixture
def celery_eager(monkeypatch):
    """Forza Celery in eager mode per la durata del test.

    Eager: `.apply()` / `.delay()` / `.apply_async()` eseguono il task
    sincronamente in-process invece di publicarlo sul broker Redis. Le
    eccezioni vengono propagate al chiamante (`task_eager_propagates`).

    Ritorna l'oggetto `celery_app` cosi' i test possono accedere ai
    task se serve.
    """
    from workers.celery_app import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)
    monkeypatch.setattr(celery_app.conf, "task_store_eager_result", True)
    return celery_app


# =============================================================================
# 2. Fake ApiConnectionService — spy sui tenant binding
# =============================================================================


class FakeApiConnectionService:
    """Replacement test-only di `ApiConnectionService`.

    Registra ogni assignment di `_current_group_id` e ogni call ai
    loader/executor in `_call_log`. La classe reale ha __slots__/property?
    No, allora non avremmo bisogno di un fake — ma usiamo comunque una
    classe vera (non MagicMock) per garantire isolamento dello state
    fra istanze (MagicMock spec stringa puo' condividere attribute set
    fra mock paralleli con magic descriptor).

    Usage:
        instances: List[FakeApiConnectionService] = []
        class Tracked(FakeApiConnectionService):
            def __init__(self):
                super().__init__()
                instances.append(self)
        monkeypatch.setattr(
            "app.core.arricchisci.api_connector.ApiConnectionService",
            Tracked,
        )
    """

    def __init__(self):
        self._call_log: List[tuple] = []
        self._group_id_assignments: List[tuple] = []
        # I task accedono a questi attributi runtime; default safe.
        self._preview_limit = None
        self._current_group_id = None

    def __setattr__(self, name: str, value: Any) -> None:
        # Registra il setattr di `_current_group_id` con un timestamp
        # ordinabile vs le call ai loader (ordine relativo, non absolute).
        if name == "_current_group_id":
            # Cerchiamo di registrarlo nel call_log se gia' inizializzato
            if "_call_log" in self.__dict__:
                self._call_log.append(("setattr:_current_group_id", value))
                self._group_id_assignments.append(
                    (len(self._call_log), value)
                )
        super().__setattr__(name, value)

    # Loader/executor: registrano la call e sollevano una sentinel
    # che permette al test di abortire l'esecuzione del task subito
    # dopo aver osservato l'invocazione (no esecuzione reale).
    def list_api_connections(self):
        self._call_log.append(("list_api_connections",))
        return []

    def get_api_connection(self, *args, **kwargs):
        self._call_log.append(("get_api_connection", args, kwargs))
        return None

    def _get_execution_order(self, nodes, edges):
        self._call_log.append(("_get_execution_order",))
        return [n["id"] for n in nodes]

    def _load_source_project(self, *args, **kwargs):
        self._call_log.append(("_load_source_project", args, kwargs))
        raise _HarnessAbort("loader-invoked", marker="_load_source_project")

    def _load_file_source(self, *args, **kwargs):
        self._call_log.append(("_load_file_source", args, kwargs))
        raise _HarnessAbort("loader-invoked", marker="_load_file_source")

    def _execute_rest_file(self, *args, **kwargs):
        self._call_log.append(("_execute_rest_file", args, kwargs))
        raise _HarnessAbort("loader-invoked", marker="_execute_rest_file")


class _HarnessAbort(Exception):
    """Sentinel: il task ha raggiunto il punto di osservazione voluto.

    Il task ha try/except generico che cattura tutto e ritorna un
    error dict. Il test interroga il side-effect (call_log) sul fake
    api_cs invece del return value.
    """

    def __init__(self, message: str, marker: str = ""):
        super().__init__(message)
        self.marker = marker


# =============================================================================
# 3. Composite harness — wiring completo per execute_enrichment_task
# =============================================================================


@dataclass
class HarnessSpies:
    """Aggregato di tutto cio' che la harness osserva durante l'invocazione."""

    api_cs_instances: List[FakeApiConnectionService] = field(default_factory=list)
    progress_calls: List[Dict[str, Any]] = field(default_factory=list)
    eseguibile_lookups: List[str] = field(default_factory=list)
    project_metadata_calls: List[str] = field(default_factory=list)
    intermediate_run_creates: List[Dict[str, Any]] = field(default_factory=list)
    saved_pipeline_results: List[Dict[str, Any]] = field(default_factory=list)
    saved_result_blobs: List[Dict[str, Any]] = field(default_factory=list)
    columns_metadata_saves: List[Dict[str, Any]] = field(default_factory=list)


def _make_tracked_api_cs(spies: HarnessSpies):
    instances = spies.api_cs_instances

    class Tracked(FakeApiConnectionService):
        def __init__(self):
            super().__init__()
            instances.append(self)

    return Tracked


def _make_fake_executor():
    """EnrichmentExecutor stub: ritorna un risultato vuoto."""

    class _FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def execute(self, *args, **kwargs):
            # Mai chiamato perche' il fake api_cs solleva _HarnessAbort
            # sui loader prima.
            return MagicMock(errors=[], rows_processed=0)

    return _FakeExecutor


def install_enrichment_task_mocks(
    monkeypatch, *, eseguibile_group_id: Optional[str] = "test-group",
    project_group_id: str = "test-group",
    final_df: Any = None,
    final_file_result: Optional[Dict[str, Any]] = None,
    is_already_completed_returns: bool = False,
    revoked_payload: Optional[Dict[str, Any]] = None,
) -> HarnessSpies:
    """Installa monkeypatch su tutte le dep di `execute_enrichment_task`.

    Niente DB, niente Redis, niente FS reale. Le call vengono registrate
    in `HarnessSpies` ritornato.

    Parametri:
        eseguibile_group_id: ritornato da `get_eseguibile`. None per
            simulare "eseguibile non trovato".
        project_group_id: ritornato da `ProjectService.get_project_metadata`.
        final_df: se non None, viene iniettato come "risultato finale"
            via patch del path DataFrame del task. Per i test che vogliono
            invocare il ramo standard end-to-end. Se None il fake api_cs
            solleva _HarnessAbort sul primo loader e il test legge i
            side-effect senza arrivare al ramo finale.
        final_file_result: idem per il ramo file_result (`is_file_result=True`).
        is_already_completed_returns: forza il return di
            `app.shared.idempotency.is_already_completed`.
        revoked_payload: se non None, `recheck_user_authorized` ritorna
            questo dict (simula utente revocato).
    """
    spies = HarnessSpies()

    # 1. Idempotency
    monkeypatch.setattr(
        "app.shared.idempotency.is_already_completed",
        lambda run_id: is_already_completed_returns,
    )

    # 2. User recheck
    monkeypatch.setattr(
        "app.shared.async_user_recheck.recheck_user_authorized",
        lambda **kwargs: revoked_payload,
    )

    # 3. Logger / run lifecycle no-op (top-level imports → patch su modulo task)
    import workers.tasks.enrichment_task as et_mod

    for fn_name in ("open_run", "close_run", "set_run_context", "append_step"):
        monkeypatch.setattr(et_mod, fn_name, lambda *a, **kw: None)

    # 4. ApiConnectionService → tracked
    monkeypatch.setattr(
        "app.core.arricchisci.api_connector.ApiConnectionService",
        _make_tracked_api_cs(spies),
    )

    # 5. EnrichmentExecutor (top-level import nel task module)
    monkeypatch.setattr(et_mod, "EnrichmentExecutor", _make_fake_executor())
    monkeypatch.setattr(et_mod, "generate_error_report", lambda *a, **kw: None)

    # 6. Eseguibile DB lookup
    def _fake_get_eseguibile(eid):
        spies.eseguibile_lookups.append(eid)
        if eseguibile_group_id is None:
            return None
        return {"group_id": eseguibile_group_id}

    # Crea/sostituisce il modulo se serve (lazy import dentro il task).
    import sys
    eseg_mod_name = "app.core.eseguibili.db"
    if eseg_mod_name in sys.modules:
        monkeypatch.setattr(
            f"{eseg_mod_name}.get_eseguibile", _fake_get_eseguibile,
        )
    else:
        # Crea modulo stub on-the-fly
        import types
        stub = types.ModuleType(eseg_mod_name)
        stub.get_eseguibile = _fake_get_eseguibile
        monkeypatch.setitem(sys.modules, eseg_mod_name, stub)
        # E assicurati che il package parent esponga l'attribute
        eseg_pkg = sys.modules.get("app.core.eseguibili")
        if eseg_pkg is not None:
            monkeypatch.setattr(eseg_pkg, "db", stub, raising=False)

    # 7. ProjectService (lazy import dentro task)
    class _FakeProjectMeta:
        def __init__(self, group_id):
            self.group_id = group_id

    class _FakeProjectService:
        def get_project_metadata(self, project_id):
            spies.project_metadata_calls.append(project_id)
            return _FakeProjectMeta(project_group_id)

    monkeypatch.setattr(
        "app.services.project_service.ProjectService", _FakeProjectService,
    )

    # 8. Intermediate run creation (lazy)
    def _fake_create_run(**kwargs):
        spies.intermediate_run_creates.append(kwargs)

    import types as _types
    idb_name = "app.core.intermediate.db"
    if idb_name in sys.modules:
        monkeypatch.setattr(f"{idb_name}.create_run", _fake_create_run)
    else:
        stub = _types.ModuleType(idb_name)
        stub.create_run = _fake_create_run
        monkeypatch.setitem(sys.modules, idb_name, stub)
        inter_pkg = sys.modules.get("app.core.intermediate")
        if inter_pkg is not None:
            monkeypatch.setattr(inter_pkg, "db", stub, raising=False)

    # 9. result_store (lazy)
    def _fake_save_pipeline_result(**kwargs):
        spies.saved_pipeline_results.append(kwargs)
        return {
            "result_id": "fake-result-id-df",
            "output_filename": kwargs.get("filename_stem", "output") + ".csv",
        }

    def _fake_save_result_blob(**kwargs):
        spies.saved_result_blobs.append(kwargs)
        return "fake-result-id-file"

    monkeypatch.setattr(
        "app.utils.result_store.save_pipeline_result", _fake_save_pipeline_result,
    )
    monkeypatch.setattr(
        "app.utils.result_store.save_result_blob", _fake_save_result_blob,
    )

    # 10. columns_metadata
    def _fake_save_cols(**kwargs):
        spies.columns_metadata_saves.append(kwargs)

    monkeypatch.setattr(
        "app.shared.columns_metadata.save_columns_metadata_with_retry",
        _fake_save_cols,
    )

    # 11. snapshot / writer.close_run (lazy intermediate)
    snap_name = "app.core.intermediate.snapshot"
    if snap_name in sys.modules:
        monkeypatch.setattr(
            f"{snap_name}.record_final_snapshot", lambda **kw: None,
        )
    else:
        stub = _types.ModuleType(snap_name)
        stub.record_final_snapshot = lambda **kw: None
        monkeypatch.setitem(sys.modules, snap_name, stub)

    writer_name = "app.core.intermediate.writer"
    if writer_name in sys.modules:
        monkeypatch.setattr(f"{writer_name}.close_run", lambda **kw: None)
    else:
        stub = _types.ModuleType(writer_name)
        stub.close_run = lambda **kw: None
        monkeypatch.setitem(sys.modules, writer_name, stub)

    # 12. _trigger_carica_if_present (module helper, no-op)
    monkeypatch.setattr(
        et_mod, "_trigger_carica_if_present", lambda **kw: None,
    )
    monkeypatch.setattr(
        et_mod, "_cleanup_temp_file_sources", lambda *a, **kw: None,
    )

    return spies


def invoke_enrichment_task(
    *, project_id: str, canvas: Dict, run_id: Optional[str] = None,
    is_eseguibile: bool = False, output_format: str = "csv",
    user_id: Optional[str] = "test-user",
) -> Dict[str, Any]:
    """Invoca `execute_enrichment_task` in eager mode e ritorna il dict.

    Richiede che `celery_eager` fixture sia gia' attiva nel test.
    `task_eager_propagates=True` propaga le eccezioni — ma la task
    ha try/except top-level che cattura tutto e ritorna un error dict,
    quindi non vediamo MAI un raise reale qui (eccetto _HarnessAbort
    che il task converte in error dict).
    """
    from workers.tasks.enrichment_task import execute_enrichment_task

    res = execute_enrichment_task.apply(
        args=[project_id, canvas, output_format, False, None,
              is_eseguibile, run_id, user_id],
    )
    return res.get(disable_sync_subtasks=False)


# =============================================================================
# 4. Spy su update_task_progress per verificare task_id propagation
# =============================================================================


def install_update_task_progress_spy(monkeypatch) -> List[Dict[str, Any]]:
    """Wrappa `update_task_progress` per registrare ogni call.

    Ritorna una lista di dict {progress, message, task_id, is_error, ...}
    riempita da ogni invocazione del task.
    """
    import workers.tasks.enrichment_task as et_mod

    calls: List[Dict[str, Any]] = []
    orig = et_mod.update_task_progress

    def _spy(task, progress, message, *, task_id=None, is_error=False, **kw):
        calls.append({
            "progress": progress,
            "message": message,
            "task_id": task_id,
            "is_error": is_error,
            "extra": kw,
        })
        # NON chiamare l'original (richiede self.request.id reale e altre
        # interazioni Celery). I test verificano solo i kwargs passati.

    monkeypatch.setattr(et_mod, "update_task_progress", _spy)
    return calls
