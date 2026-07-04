"""
Harness test-only per l'invocazione diretta di execute_enrichment_task.

I corpi dei task sono funzioni pure (Plenora 2.0 Fase 5): i test li
invocano direttamente via ``invoke_enrichment_task`` (con un self-shim
request.id/name) e osservano i side-effect sulle spie installate da
``install_enrichment_task_mocks``. Rinominato da ``celery_eager.py``
(2026-07-04): il fixture no-op ``celery_eager`` e' stato rimosso insieme
allo stack Celery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# 1. Fake ApiConnectionService — spy sui tenant binding


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


# 3. Composite harness — wiring completo per execute_enrichment_task


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
    output_contract_saves: List[Dict[str, Any]] = field(default_factory=list)
    # Run-a-completamento (final_df): spie su finalize + output divergente.
    eseg_saves: List[Dict[str, Any]] = field(default_factory=list)
    logger_close_runs: List[Dict[str, Any]] = field(default_factory=list)
    append_steps: List[Dict[str, Any]] = field(default_factory=list)
    intermediate_close_runs: List[Dict[str, Any]] = field(default_factory=list)
    final_snapshots: List[Dict[str, Any]] = field(default_factory=list)
    carica_triggers: List[Dict[str, Any]] = field(default_factory=list)
    debug_payloads: List[Dict[str, Any]] = field(default_factory=list)


def _make_tracked_api_cs(spies: HarnessSpies, final_df: Any = None):
    instances = spies.api_cs_instances

    class Tracked(FakeApiConnectionService):
        def __init__(self):
            super().__init__()
            instances.append(self)

        def _load_source_project(self, *args, **kwargs):
            # Run-a-completamento: se l'harness fornisce un final_df, il source
            # lo ritorna (il nodo result lo propaga → il task raggiunge il
            # finalize). Altrimenti comportamento legacy: abort sul loader.
            self._call_log.append(("_load_source_project", args, kwargs))
            if final_df is not None:
                return final_df
            raise _HarnessAbort("loader-invoked", marker="_load_source_project")

    return Tracked


def _make_build_enrichment_executor_stub():
    """Stub di `build_enrichment_executor`: factory che ritorna un fake
    executor con l'interfaccia usata dall'enrich_fn del task
    (`execute` -> (df, result), `options`, `api_config`,
    `detected_column_types`). Per i canvas senza nodi api_enrich non viene
    mai chiamato; per gli altri ritorna un risultato vuoto coerente.
    """

    class _FakeOptions:
        continue_on_error = True
        stop_on_error_threshold = 0
        generate_error_report = False

    class _FakeStats:
        success = 0
        total_rows = 0
        cached = 0
        errors = 0

        def to_dict(self):
            return {}

    class _FakeEnrichmentResult:
        def __init__(self):
            self.stats = _FakeStats()
            self.errors = []

    class _FakeExecutor:
        def __init__(self, api_config):
            self.api_config = api_config or {}
            self.options = _FakeOptions()
            self.detected_column_types = {}

        def execute(self, df):
            return df, _FakeEnrichmentResult()

    def _stub(node_data, api_config, *, progress_callback=None):
        return _FakeExecutor(api_config)

    return _stub


def install_enrichment_task_mocks(
    monkeypatch, *, eseguibile_group_id: Optional[str] = "test-group",
    project_group_id: str = "test-group",
    final_df: Any = None,
    is_already_completed_returns: bool = False,
    revoked_payload: Optional[Dict[str, Any]] = None,
    persist_raises: bool = False,
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
        is_already_completed_returns: forza il return di
            `app.core.intermediate.run_idempotency.is_already_completed`.
        revoked_payload: se non None, `recheck_user_authorized` ritorna
            questo dict (simula utente revocato).
    """
    spies = HarnessSpies()

    # 1. Idempotency
    monkeypatch.setattr(
        "app.core.intermediate.run_idempotency.is_already_completed",
        lambda run_id: is_already_completed_returns,
    )

    # 2. User recheck
    monkeypatch.setattr(
        "app.core.auth.async_user_recheck.recheck_user_authorized",
        lambda **kwargs: revoked_payload,
    )

    # 3. Logger / run lifecycle no-op (top-level imports → patch su modulo task)
    import workers.tasks.enrichment_task as et_mod

    monkeypatch.setattr(et_mod, "open_run", lambda *a, **kw: None)
    monkeypatch.setattr(et_mod, "set_run_context", lambda *a, **kw: None)
    monkeypatch.setattr(
        et_mod, "close_run", lambda *a, **kw: spies.logger_close_runs.append(kw),
    )
    monkeypatch.setattr(
        et_mod, "append_step",
        lambda *a, **kw: spies.append_steps.append(a[0] if a else kw),
    )

    # Fake eseguibili storage (ramo eseguibile run-a-completamento). Patchato al
    # SOURCE (non su et_mod) così che sia task che enrichment_run_mode vedano il
    # fake. Consuma i chunk del generator per non lasciare thread producer appesi.
    import app.core.eseguibili.storage as _eseg_src

    def _fake_eseg_save_chunks(task_id, filename, chunks):
        for _ in chunks:
            pass
        spies.eseg_saves.append(
            {"task_id": task_id, "filename": filename, "kind": "chunks"}
        )

    def _fake_eseg_save_bytes(task_id, filename, data):
        spies.eseg_saves.append(
            {"task_id": task_id, "filename": filename, "kind": "bytes"}
        )

    monkeypatch.setattr(_eseg_src, "save_chunks", _fake_eseg_save_chunks, raising=False)
    monkeypatch.setattr(_eseg_src, "save_bytes", _fake_eseg_save_bytes, raising=False)

    # 4. ApiConnectionService → tracked (con final_df per run-a-completamento)
    monkeypatch.setattr(
        "app.core.arricchisci.api_connector.ApiConnectionService",
        _make_tracked_api_cs(spies, final_df),
    )

    # 5. build_enrichment_executor (factory usata dall'enrich_fn del task)
    monkeypatch.setattr(
        et_mod, "build_enrichment_executor",
        _make_build_enrichment_executor_stub(),
    )
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

        def get_project_output_contract(self, project_id):
            return None

        def update_project_output_contract(self, project_id, output_contract):
            spies.output_contract_saves.append({
                "project_id": project_id,
                "output_contract": output_contract,
            })
            return True

    monkeypatch.setattr(
        "app.core.projects.service.ProjectService", _FakeProjectService,
    )

    # 8. Intermediate run creation (lazy)
    def _fake_create_run(**kwargs):
        spies.intermediate_run_creates.append(kwargs)

    import types as _types
    lifecycle_name = "app.core.intermediate.run_lifecycle"
    if lifecycle_name in sys.modules:
        monkeypatch.setattr(f"{lifecycle_name}.create_run", _fake_create_run)
    else:
        stub = _types.ModuleType(lifecycle_name)
        stub.create_run = _fake_create_run
        # enrichment_task importa sia `create_run` che
        # `get_default_runs_retention` dallo stesso modulo (linea 202-205).
        # Senza il secondo simbolo l'import fallisce e il `try` esterno
        # cattura tutto, lasciando `_group_id = ""` → ctx.group_id=None
        # → fail-closed sul primo loader.
        stub.get_default_runs_retention = lambda: None
        monkeypatch.setitem(sys.modules, lifecycle_name, stub)

    # 9. result persistence (lazy)
    def _fake_save_pipeline_result_outcome(**kwargs):
        from app.core.results.pipeline_result import PipelineResultSave

        spies.saved_pipeline_results.append(kwargs)
        if persist_raises:
            raise RuntimeError("persist boom (harness persist_raises)")
        return PipelineResultSave(
            result_id="fake-result-id-df",
            output_filename=kwargs.get("filename_stem", "output") + ".csv",
        )

    def _fake_save_result_blob(**kwargs):
        spies.saved_result_blobs.append(kwargs)
        return "fake-result-id-file"

    monkeypatch.setattr(
        "app.core.results.pipeline_result.save_pipeline_result_outcome",
        _fake_save_pipeline_result_outcome,
    )
    monkeypatch.setattr(
        "app.core.results.blob_writer.save_result_blob_from_bytes",
        _fake_save_result_blob,
    )

    # 10. snapshot / writer.close_run (lazy intermediate)
    _rec_snapshot = lambda **kw: spies.final_snapshots.append(kw)
    snap_name = "app.core.intermediate.snapshot"
    if snap_name in sys.modules:
        monkeypatch.setattr(f"{snap_name}.record_final_snapshot", _rec_snapshot)
    else:
        stub = _types.ModuleType(snap_name)
        stub.record_final_snapshot = _rec_snapshot
        monkeypatch.setitem(sys.modules, snap_name, stub)

    _rec_iclose = lambda **kw: spies.intermediate_close_runs.append(kw)
    writer_name = "app.core.intermediate.writer"
    if writer_name in sys.modules:
        monkeypatch.setattr(f"{writer_name}.close_run", _rec_iclose)
    else:
        stub = _types.ModuleType(writer_name)
        stub.close_run = _rec_iclose
        monkeypatch.setitem(sys.modules, writer_name, stub)

    # 11. DebugPayloadBuilder (failure finalize) — patch al SOURCE così il task
    # (che usa `debug_payload_builder_module.DebugPayloadBuilder`) vede il fake.
    class _FakeDebugPayloadBuilder:
        def build(self, *a, **kw):
            spies.debug_payloads.append(kw or {"args": a})
            return {}

    monkeypatch.setattr(
        "app.utils.debug_payload_builder.DebugPayloadBuilder",
        _FakeDebugPayloadBuilder,
        raising=False,
    )

    # 12. _trigger_carica_if_present (module helper)
    monkeypatch.setattr(
        et_mod, "_trigger_carica_if_present",
        lambda **kw: spies.carica_triggers.append(kw),
    )
    monkeypatch.setattr(
        et_mod, "_cleanup_temp_file_sources", lambda *a, **kw: None,
    )

    # 13. update_task_progress no-op default. La call site sottostante
    # scriverebbe il progress su PostgreSQL (task_results), non
    # disponibile negli unit test. I test che vogliono osservare le call
    # possono ridichiararla via `install_update_task_progress_spy`
    # (chiamata DOPO questo helper), che sovrascrive questo no-op.
    monkeypatch.setattr(
        et_mod, "update_task_progress", lambda *a, **kw: None,
    )

    return spies


def invoke_enrichment_task(
    *, project_id: str, canvas: Dict, run_id: Optional[str] = None,
    is_eseguibile: bool = False, output_format: str = "csv",
    user_id: Optional[str] = "test-user",
) -> Dict[str, Any]:
    """Invoca `execute_enrichment_task` (funzione pura) e ritorna il dict.

    Plenora 2.0 Fase 5: chiamata diretta con un self-shim (request.id/name)
    al posto di `.apply()` eager Celery. Il task ha try/except top-level che
    cattura tutto e ritorna un error dict (anche _HarnessAbort).
    """
    from types import SimpleNamespace

    from workers.tasks.enrichment_task import execute_enrichment_task

    shim = SimpleNamespace(
        request=SimpleNamespace(id=run_id or "test-task"),
        name="tasks.execute_enrichment",
    )
    return execute_enrichment_task(
        shim, project_id, canvas, output_format, False, None,
        is_eseguibile, run_id, user_id,
    )


# 4. Spy su update_task_progress per verificare task_id propagation


def install_update_task_progress_spy(monkeypatch) -> List[Dict[str, Any]]:
    """Wrappa `update_task_progress` per registrare ogni call.

    Ritorna una lista di dict {progress, message, task_id, is_error, ...}
    riempita da ogni invocazione del task.
    """
    import workers.tasks.enrichment_task as et_mod

    calls: List[Dict[str, Any]] = []

    def _spy(task, progress, message, *, task_id=None, is_error=False, **kw):
        calls.append({
            "progress": progress,
            "message": message,
            "task_id": task_id,
            "is_error": is_error,
            "extra": kw,
        })
        # NON chiamare l'original (scriverebbe il progress su PG via
        # report_task_progress). I test verificano solo i kwargs passati.

    monkeypatch.setattr(et_mod, "update_task_progress", _spy)
    return calls
