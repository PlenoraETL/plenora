"""Contract tests per il tenant binding del task `execute_enrichment_task`.

Il task deve:
1. instanziare una `ApiConnectionService()` locale, non usare un
   singleton condiviso fra task concorrenti;
2. impostare `_current_group_id` prima di qualunque loader/executor che
   applica controlli fail-closed;
3. propagare gli identificativi (`result_id`, `task_id`) necessari ai
   consumer downstream.

Questi test usano `inspect.getsource` per bloccare i pattern strutturali
critici senza dover costruire un test runtime completo del task Celery.
"""

import inspect

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.security,
    pytest.mark.regression,
]


def test_execute_enrichment_task_does_not_import_singleton():
    """Il celery task NON deve importare il singleton modulo-level
    `api_connection_service`. Il singleton ha stato per-execution
    (`_current_group_id`, `_preview_limit`) condiviso fra task
    concorrenti sullo stesso worker process — race condition
    cross-tenant. Deve invece importare la classe e instanziare."""
    from workers.tasks import enrichment_task

    source = inspect.getsource(enrichment_task)

    # NON deve esserci nessun import del singleton dentro execute_enrichment_task
    # (controlliamo l'intero modulo perche' anche un import nascosto in helper
    # secondari riprodurrebbe il bug)
    assert "import api_connection_service" not in source, (
        "Il task celery non deve importare il "
        "singleton globale `api_connection_service`. Stato per-execution "
        "(`_current_group_id`) e' soggetto a race condition cross-tenant "
        "fra task paralleli sullo stesso worker. Importare invece la "
        "classe `ApiConnectionService` e instanziare localmente."
    )


def test_execute_enrichment_task_instantiates_local_api_service():
    """Il task DEVE importare la classe e creare un'istanza locale."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    source = inspect.getsource(execute_enrichment_task)

    assert "import ApiConnectionService" in source, (
        "Il task deve importare la classe `ApiConnectionService` "
        "(non il singleton)"
    )
    assert "ApiConnectionService()" in source, (
        "Il task deve creare un'istanza locale `ApiConnectionService()` "
        "per garantire isolamento del tenant binding fra task concorrenti"
    )


def test_execute_enrichment_task_sets_current_group_id_before_loaders():
    """Il task DEVE settare `_current_group_id` sull'istanza locale.
    Senza questo, i metodi `_load_source_project`, `_load_file_source`,
    `_execute_rest_file` falliscono con
    'Contesto di sicurezza non determinabile per file_source' (fail-closed).
    """
    from workers.tasks.enrichment_task import execute_enrichment_task

    source = inspect.getsource(execute_enrichment_task)

    assert "_current_group_id" in source, (
        "Il task deve settare `_current_group_id` "
        "sull'istanza locale `api_connection_service` prima di chiamare "
        "i metodi che richiedono tenant binding (`_load_source_project`, "
        "`_load_file_source`, `_execute_rest_file`)."
    )

    # E deve essere settato PRIMA del primo loader. Verifica posizionale
    # cercando l'assegnazione `._current_group_id =` (non solo il nome
    # nudo che potrebbe apparire nei commenti) e le call ai loader con
    # parentesi aperta `(`.
    init_idx = source.find("._current_group_id =")
    first_loader_idx = min(
        (source.find(name) for name in (
            "._load_source_project(",
            "._load_file_source(",
            "._execute_rest_file(",
        ) if source.find(name) != -1),
        default=-1,
    )
    assert init_idx != -1, "non trovo l'assegnazione `_current_group_id`"
    assert first_loader_idx != -1, "Il task non chiama nessun loader?"
    assert init_idx < first_loader_idx, (
        "`_current_group_id` deve essere settato PRIMA della prima "
        "chiamata a un loader/executor (altrimenti fail-closed)"
    )


def test_execute_enrichment_task_propagates_result_id_in_dataframe_branch():
    """Regression guard: il ramo DataFrame del task celery DEVE includere
    `result_id` nel dict ritornato. Senza, `/api/tasks/{id}/download` cade
    su `task_info.result["result_id"]` → None → 410 Gone (vedi
    orchestrator/router.py:580). E' lo stesso bug di catena gia' fixato
    in `api_connector._save_and_build_dataframe_response` /
    `EnrichExecutor.execute`, qui riaperto a livello celery task."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    source = inspect.getsource(execute_enrichment_task)

    # Cerca il pattern del result dict nel ramo dataframe.
    # Deve includere `"result_id":` come chiave.
    df_branch_marker = "rows_processed\": len(final_df)"
    df_idx = source.find(df_branch_marker)
    assert df_idx != -1, "ramo dataframe result dict non trovato"

    # Cerca `"result_id":` nella stessa zona (entro ~1500 char dal marker)
    nearby = source[max(0, df_idx - 1500):df_idx + 500]
    assert '"result_id"' in nearby, (
        "Il ramo DataFrame del task DEVE includere `result_id` nel result "
        "dict, altrimenti il download via /api/tasks/{id}/download cade su "
        "410 Gone (orchestrator/router.py:580 usa task_info.result['result_id'])"
    )


def test_execute_enrichment_task_propagates_result_id_in_file_branch():
    """Regression guard: il ramo file_result (rest_file con
    `is_file_result=True`) DEVE includere `result_id` nel dict ritornato.
    Stesso bug del ramo DataFrame (orchestrator/router.py:580)."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    source = inspect.getsource(execute_enrichment_task)

    # Il ramo file_result ha "output_type": "file" come marker
    file_branch_marker = '"output_type": "file"'
    file_idx = source.find(file_branch_marker)
    assert file_idx != -1, "ramo file_result dict non trovato"

    # `"result_id"` deve essere nello stesso dict letterale (entro ~500 char)
    nearby = source[max(0, file_idx - 500):file_idx + 500]
    assert '"result_id"' in nearby, (
        "Il ramo file_result del task DEVE includere `result_id` nel result "
        "dict per il download /api/tasks/{id}/download (410 Gone altrimenti)"
    )


def test_update_task_progress_accepts_explicit_task_id():
    """Regression guard: `update_task_progress` deve accettare un
    `task_id` keyword esplicito che propaga a `task.update_state(task_id=...)`.

    Senza, le chiamate da dentro `_run_single_node` quando eseguito in
    un thread del `ThreadPoolExecutor` vedono `self.request.id == None`
    (Celery `request` e' thread-local), e
    `backend.store_result(task_id=None, ...)` solleva
    "task_id must not be empty. Got None instead." → tutto il task
    fallisce mentre l'enrichment era gia' completato con successo."""
    import inspect
    from workers.tasks.enrichment_task import update_task_progress

    sig = inspect.signature(update_task_progress)
    assert "task_id" in sig.parameters, (
        "`update_task_progress` deve accettare `task_id` keyword "
        "(regression guard per crash 'task_id must not be empty')"
    )
    # task_id deve essere keyword-only (oltre il `*` separator)
    param = sig.parameters["task_id"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "`task_id` deve essere keyword-only per evitare ambiguita' di chiamata"
    )

    # Stub Task: cattura task_id passato a update_state
    class _StubTask:
        def __init__(self):
            self.captured = {}
        def update_state(self, **kwargs):
            self.captured = kwargs

    stub = _StubTask()
    update_task_progress(stub, 50, "test", task_id="task-xyz")

    assert stub.captured.get("task_id") == "task-xyz", (
        "update_task_progress deve propagare task_id a task.update_state"
    )


def test_execute_enrichment_task_propagates_task_id_in_progress_calls():
    """Regression guard: tutti i call site di `update_task_progress`
    dentro `execute_enrichment_task` devono passare `task_id=task_id`
    esplicito. Senza, quelli eseguiti in thread spawned (loop parallelo
    via ThreadPoolExecutor) crashano con
    'task_id must not be empty. Got None instead.'.
    """
    import ast
    import inspect
    import textwrap
    from workers.tasks.enrichment_task import execute_enrichment_task

    # `inspect.getsource` su una funzione decorata @celery_app.task
    # mantiene l'indentazione originale, dedentiamo per ast.parse.
    source = textwrap.dedent(inspect.getsource(execute_enrichment_task))
    tree = ast.parse(source)

    bad_lines = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "update_task_progress"):
            continue
        kw_names = {k.arg for k in node.keywords if k.arg}
        if "task_id" not in kw_names:
            bad_lines.append(node.lineno)

    assert not bad_lines, (
        f"{len(bad_lines)} chiamate di update_task_progress senza "
        f"`task_id=` keyword esplicito (crash garantito nei thread "
        f"paralleli del ThreadPoolExecutor). Linee: {bad_lines}"
    )


def test_execute_enrichment_task_eseguibile_branch_fetches_group_id():
    """Regression guard BUG-ESEG-GROUP: il branch `is_eseguibile=True`
    deve recuperare il `_group_id` dalla tabella `eseguibili` (gli
    eseguibili NON sono in `projects`, quindi `ProjectService.
    get_project_metadata` ritornerebbe None).

    Prima del fix, `_group_id = ""` rimaneva vuoto e veniva propagato
    come `None` al `_current_group_id` dell'instanza locale di
    `ApiConnectionService`, facendo fallire il primo `_load_*` con
    'Contesto di sicurezza non determinabile (group_id mancante)'."""
    from workers.tasks.enrichment_task import execute_enrichment_task

    source = inspect.getsource(execute_enrichment_task)

    # Cerca l'import di get_eseguibile dentro il branch is_eseguibile
    assert "from app.core.eseguibili.db import get_eseguibile" in source, (
        "BUG-ESEG-GROUP: il branch is_eseguibile deve importare "
        "`get_eseguibile` per recuperare il group_id dalla tabella "
        "eseguibili (gli eseguibili non sono in projects)"
    )

    # Verifica posizionale: l'import e l'assegnazione di _group_id
    # devono essere PRIMA dell'init di api_connection_service._current_group_id
    eseg_lookup_idx = source.find("get_eseguibile")
    api_init_idx = source.find("._current_group_id =")
    assert eseg_lookup_idx != -1 and api_init_idx != -1
    assert eseg_lookup_idx < api_init_idx, (
        "`get_eseguibile` deve essere chiamato PRIMA di settare "
        "`_current_group_id` su api_connection_service"
    )


def test_execute_enrichment_task_imdf_branch_has_no_undefined_locals():
    """Regression guard BUG-IMDF-META: il branch di estrazione metadati
    IMDF referenziava `files_list` e `geojson_layers` — variabili
    leftover di una versione precedente, mai definite nel scope. Il
    try/except le catturava in NameError e convertiva l'imdf_metadata
    in `{"error": "name 'files_list' is not defined"}`. Conseguenza:
    il frontend non vedeva mai site_id/has_gdb/etc. di un bundle IMDF
    appena creato, anche se il task completava con successo.

    Test statico via grep sul source: i nomi morti non devono apparire."""
    from workers.tasks import enrichment_task

    source = inspect.getsource(enrichment_task)

    forbidden_names = ("files_list", "geojson_layers")
    for name in forbidden_names:
        assert name not in source, (
            f"BUG-IMDF-META regression: variabile '{name}' re-introdotta "
            f"in workers/tasks/enrichment_task.py. Era leftover di una "
            f"versione precedente: NameError catturato dal try/except del "
            f"branch IMDF metadata, imdf_metadata diventa "
            f"{{'error': ...}} invece dei metadati validi."
        )


def test_execute_enrichment_task_module_has_no_orphan_singleton_helpers():
    """Le vecchie funzioni modulo-level `get_execution_order`,
    `load_source_project`, `load_file_source`, `load_static_values` —
    che importavano il singleton globale — devono essere state rimosse.
    Se qualcuno le re-introducesse riproducendo il pattern singleton,
    questo test le cattura."""
    from workers.tasks import enrichment_task

    forbidden_helpers = (
        "get_execution_order",
        "load_source_project",
        "load_file_source",
        "load_static_values",
    )
    for name in forbidden_helpers:
        # Le funzioni non devono esistere come simbolo modulo-level
        assert not hasattr(enrichment_task, name), (
            f"Helper modulo-level `{name}` re-introdotto in "
            f"workers/tasks/enrichment_task.py. Usavano il singleton "
            f"globale `api_connection_service` con stato per-execution "
            f"non isolato — race condition cross-tenant. Chiamare "
            f"direttamente i metodi sull'istanza locale del task."
        )
