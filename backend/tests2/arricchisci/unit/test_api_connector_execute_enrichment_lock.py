"""
Lock tests per ApiConnectionService.execute_enrichment.

Locks backend contracts and regression boundaries for the current architecture.
"""

from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Builders per nodi / edges canvas


def _project_source(nid="src-1", project_id="p-up"):
    return {"id": nid, "type": "project_source", "source_project_id": project_id}


def _file_source(nid="fs-1", fid="file-1"):
    return {"id": nid, "type": "file_source", "file_source_id": fid}


def _static_values(nid="sv-1", fields=None):
    return {
        "id": nid,
        "type": "staticValues",
        "fields": fields or [{"name": "x", "value": "1"}],
    }


def _api_enrich(nid="ae-1", api_id="api-1", param_mapping=None):
    return {
        "id": nid,
        "type": "api_enrich",
        "api_id": api_id,
        "param_mapping": param_mapping or {},
    }


def _result(nid="r-1", fileName="out", fmt="csv"):
    return {"id": nid, "type": "result", "fileName": fileName, "format": fmt}


def _edge(source, target):
    return {"source": source, "target": target}


def _canvas(nodes=None, edges=None):
    return {"nodes": nodes or [], "edges": edges or []}


# Fixture: patch tutti i collaborator di execute_enrichment


@pytest.fixture
def env(api_service):
    """Fixture unificata: api_service con i collaborator diretti patchati.

    Accesso via `env.svc` (il servizio) ed `env.<mock_name>` per le
    asserzioni. Default side_effect ragionevoli per i loader (ritornano
    un DataFrame vuoto) e per i saver (ritornano un path).
    """
    api_service._current_group_id = "grp-1"

    stack = ExitStack()

    def _p(target, **kwargs):
        return stack.enter_context(patch(target, **kwargs))

    def _po(attr, **kwargs):
        return stack.enter_context(patch.object(api_service, attr, **kwargs))

    with stack:
        mocks = SimpleNamespace(
            svc=api_service,
            list_conns=_po("list_api_connections_decrypted", return_value={}),
            load_proj=_po(
                "_load_source_project",
                return_value=pd.DataFrame([{"a": 1}]),
            ),
            load_file=_po(
                "_load_file_source", return_value=pd.DataFrame([{"a": 1}])
            ),
            load_static=_po(
                "_load_static_values", return_value=pd.DataFrame([{"a": 1}])
            ),
            enricher=_po(
                "_execute_api_enricher",
                return_value=pd.DataFrame([{"a": 1, "enriched": "ok"}]),
            ),
            generator=_po(
                "_execute_api_generator",
                return_value=pd.DataFrame([{"gen": "ok"}]),
            ),
            rest_file=_po(
                "_execute_rest_file",
                return_value=pd.DataFrame([{"rf": "ok"}]),
            ),
            # B3-B.9 cleanup: rimossi 3 patches dead post-B3-B extraction.
            #   - `app.core.arricchisci.api_connector.save_dataframe` (0 usages
            #     in test): post-extraction `_save_and_build_dataframe_response`
            #     vive nel SupportMixin (B3-B.3) e usa `save_pipeline_result`
            #     lazy import — il binding api_connector.save_dataframe non
            #     veniva mai invocato.
            #   - `app.core.arricchisci.api_connector.save_result_blob`: il
            #     binding alias api_connector e' dead post-extraction; la
            #     canonical via `app.core.results.result_store.save_result_blob_from_bytes`
            #     resta patchata sotto (`save_blob_bytes`) e copre il code
            #     path csv/xlsx/json/parquet/geojson (in-memory).
            #   - `app.core.arricchisci.api_connector.get_temp_output_path`
            #     (0 usages): mai invocato post-extraction.
            #
            # Per scenari path-based (gpkg/shp) un eventuale futuro test deve
            # patchare il canonical `app.core.results.result_store.save_result_blob`,
            # NON l'alias re-import ormai rimosso da api_connector.
            save_blob_bytes=_p(
                "app.core.results.result_store.save_result_blob_from_bytes",
                return_value="res-stubbed-id",
            ),
            save_df_bytes=_p(
                "app.core.files.dataframe_io.save_dataframe_to_bytes",
                return_value=b"stub-bytes",
            ),
            project_access_ctx=_p(
                "app.core.arricchisci.execution_context.load_project_access_context",
                return_value=SimpleNamespace(project_id="proj-1", group_id="grp-1"),
            ),
        )
        yield mocks


# Section 1: casi limite (empty canvas, exception top-level)


class TestExecuteEnrichmentBasics:
    def test_empty_canvas_returns_error(self, env):
        result = env.svc.execute_enrichment("proj-1", _canvas())

        assert result["success"] is False
        assert "Nessun nodo nel canvas" in result["error"]

    def test_top_level_exception_returns_safe_error_dict(self, env):
        # Contract: una Exception non-ValueError al
        # top-level NON deve leakare il messaggio raw al caller (FE/UI).
        # Il dettaglio tecnico resta nei log; il caller riceve un messaggio
        # generico e `user_error=False` per distinguerlo da errori di
        # input/business gia' user-safe (vedi test ValueError sotto).
        env.list_conns.side_effect = RuntimeError("db down")

        result = env.svc.execute_enrichment(
            "proj-1", _canvas(nodes=[_project_source()])
        )

        assert result["success"] is False
        assert result["user_error"] is False
        assert "db down" not in result["error"]
        assert result["error"] == "Errore interno durante l'arricchimento"

    def test_top_level_value_error_propagates_message_as_user_error(self, env):
        # Contract: un ValueError sollevato dentro
        # api_connector porta un messaggio gia' user-safe (es. "Accesso
        # negato:", "URL bloccato:", validazione param). Va propagato
        # invariato al caller con `user_error=True`.
        env.list_conns.side_effect = ValueError("Accesso negato: gruppo X")

        result = env.svc.execute_enrichment(
            "proj-1", _canvas(nodes=[_project_source()])
        )

        assert result["success"] is False
        assert result["user_error"] is True
        assert "Accesso negato: gruppo X" in result["error"]

    def test_tenant_group_id_threads_through_execution_context(self, env):
        env.project_access_ctx.return_value = SimpleNamespace(
            project_id="proj-1",
            group_id="grp-xyz",
        )

        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result()],
                edges=[_edge("src-1", "r-1")],
            ),
        )

        _, kwargs = env.load_proj.call_args
        assert kwargs["ctx"].group_id == "grp-xyz"
        assert env.svc._current_group_id == "grp-1"


# Section 2: loading dei nodi sorgente


class TestExecuteEnrichmentSourceNodes:
    def test_project_source_invokes_load_source_project(self, env):
        node = _project_source("src-1", project_id="p-upstream")
        env.svc.execute_enrichment(
            "proj-1",
            _canvas(nodes=[node, _result()], edges=[_edge("src-1", "r-1")]),
        )

        env.load_proj.assert_called_once()
        args, _ = env.load_proj.call_args
        assert args[0]["source_project_id"] == "p-upstream"

    def test_project_source_force_recalculate_propagated(self, env):
        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result()],
                edges=[_edge("src-1", "r-1")],
            ),
            force_recalculate=True,
        )

        args, kwargs = env.load_proj.call_args
        # force_recalculate e' posizionale (secondo arg) nel call site
        assert args[1] is True or kwargs.get("force_recalculate") is True

    def test_file_source_standard_invokes_load_file_source(self, env):
        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_file_source(), _result()],
                edges=[_edge("fs-1", "r-1")],
            ),
        )

        env.load_file.assert_called_once()

    def test_static_values_invokes_load_static_values(self, env):
        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), _result()],
                edges=[_edge("sv-1", "r-1")],
            ),
        )

        env.load_static.assert_called_once()


# Section 3: dispatch api_enrich → enricher/generator/rest_file


class TestExecuteEnrichmentApiEnrichDispatch:
    def test_enricher_api_type_invokes_execute_api_enricher(self, env):
        env.list_conns.return_value = {
            "api-1": {"api_type": "enricher", "connection_type": "manual"},
        }

        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), _api_enrich(), _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        env.enricher.assert_called_once()
        env.generator.assert_not_called()

    def test_generator_api_type_invokes_execute_api_generator(self, env):
        env.list_conns.return_value = {
            "api-1": {"api_type": "generator", "connection_type": "manual"},
        }

        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), _api_enrich(), _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        env.generator.assert_called_once()
        env.enricher.assert_not_called()

    def test_enricher_valueerror_in_parallel_branch_is_blocking(self, env):
        """CONTRATTO: un ValueError sollevato da `_execute_api_enricher`
        dentro un parallel group deve far FALLIRE la pipeline.

        Prima dell'aprile 2026 il comportamento degradava a success:
        l'errore veniva inghiottito e il final_result tornava il
        DataFrame upstream (staticValues), facendo uscire la pipeline
        con success ma con output passthrough che mascherava l'errore
        API. Cfr. `test_backend_api_enrich_http_failure_flow` per il
        funzionale che ha portato al fix."""
        env.list_conns.return_value = {
            "api-1": {"api_type": "enricher", "connection_type": "manual"},
        }
        env.enricher.side_effect = ValueError("param mapping mismatch")

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), _api_enrich(), _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        # La pipeline DEVE fallire: non si puo' "continuare con il
        # fallback upstream" quando un api_enrich upstream del result
        # ha fallito — significa silent data loss nel result finale.
        assert result["success"] is False
        assert "param mapping mismatch" in (result.get("error") or "")

    def test_generator_in_single_step_invokes_execute_api_generator(self, env):
        """PR #22 regression test.

        RB.#15 (`compute_parallel_groups`) flusha gli api_enrich
        cardinality-changing (output_mapping con `[*]`/`[]`) come step
        ("single", [node_id]) invece di accumularli nel batch
        ("parallel", ...). Prima di PR #22 il branch `step_type ==
        "single"` di `execute_enrichment` (api_connector.py:421-454)
        non aveva un handler per `node_type == 'api_enrich'`: il nodo
        veniva silenziosamente saltato, l'API non veniva mai chiamata
        e `_find_final_result` cadeva su un DataFrame upstream
        (staticValues), facendo uscire la pipeline con success=True
        mascherando errori HTTP del generator.

        Questo test costruisce esattamente il flow problematico:
        `staticValues -> api_enrich(generator + output_mapping[*]) -> result`
        e verifica che `_execute_api_generator` venga effettivamente
        invocato (=> il branch single api_enrich e' attivo).
        """
        env.list_conns.return_value = {
            "api-1": {
                "api_type": "generator",
                "connection_type": "manual",
                # output_mapping con `[*]` triggera RB.#15 →
                # cardinality-changing → emit ("single", [ae-1]).
                "output_mapping": [
                    {"path": "data[*].value", "column": "value"}
                ],
            },
        }

        # Costruzione esplicita del nodo api_enrich con output_mapping
        # contenente `[*]` (helper `_api_enrich` di base non lo include).
        ae_node = {
            "id": "ae-1",
            "type": "api_enrich",
            "api_id": "api-1",
            "param_mapping": {},
            "output_mapping": [
                {"path": "data[*].value", "column": "value"}
            ],
        }

        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), ae_node, _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        # Pre-PR #22: assert_called_once() falliva (mai chiamato).
        # Post-PR #22: il branch single dispatcha a _run_single_enricher
        # che chiama _execute_api_generator.
        env.generator.assert_called_once()
        env.enricher.assert_not_called()

    def test_generator_in_single_step_propagates_http_error_as_failure(self, env):
        """PR #22 regression test.

        Quando `_execute_api_generator` solleva ValueError (es. HTTP 422
        dal generator API, vedi `_call_generator_endpoint:480-482`),
        il branch single api_enrich di PR #22 deve propagare l'errore
        attraverso il boundary `execute_enrichment:556-561` che lo
        cattura come `{success: False, user_error: True, error: ...}`.

        Pre-PR #22: il nodo veniva saltato silenziosamente → success=True
        con DataFrame upstream → bug funzionale documentato in
        `test_backend_api_enrich_http_failure_flow`.
        """
        env.list_conns.return_value = {
            "api-1": {
                "api_type": "generator",
                "connection_type": "manual",
                "output_mapping": [
                    {"path": "data[*].value", "column": "value"}
                ],
            },
        }
        env.generator.side_effect = ValueError(
            "API error: HTTP 422 — invalid payload"
        )

        ae_node = {
            "id": "ae-1",
            "type": "api_enrich",
            "api_id": "api-1",
            "param_mapping": {},
            "output_mapping": [
                {"path": "data[*].value", "column": "value"}
            ],
        }

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), ae_node, _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        # ValueError sollevato dal generator deve emergere come failure.
        # Niente fallback su DataFrame upstream (silent data loss).
        assert result["success"] is False
        assert result.get("user_error") is True
        assert "HTTP 422" in (result.get("error") or "")


# Section 5: risultato finale (dataframe / file / fallback / persistence)


class TestExecuteEnrichmentFinalResult:
    def test_dataframe_result_saves_blob_and_returns_shape(self, env):
        env.load_proj.return_value = pd.DataFrame(
            [{"a": 1, "b": 2}, {"a": 3, "b": 4}]
        )

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result(fmt="csv", fileName="out")],
                edges=[_edge("src-1", "r-1")],
            ),
        )

        assert result["success"] is True
        assert result["rows_count"] == 2
        assert result["columns_count"] == 2
        # `result_id` e' il riferimento canonico al blob salvato; i campi
        # NOTE: legacy `output_file`/`output_path` sono stati rimossi perche'
        # il file temp non esiste piu' dopo `save_result_blob*`.
        assert "result_id" in result
        assert "output_filename" in result
        # Post-refactor in-memory: per csv (default) passa da
        # save_result_blob_from_bytes. B3-B.9 cleanup: il path-based
        # `save_result_blob` non e' piu' patchato via alias api_connector
        # (dead post-extraction); il default csv usa solo save_blob_bytes.
        assert env.save_blob_bytes.call_count == 1

    def test_dataframe_result_does_NOT_emit_stale_output_path(self, env):
        """Regression guard: i campi legacy `output_path`/`output_file`
        non devono comparire nel response dict, perche' dopo
        `save_result_blob` puntano a file gia' unlink-ati. Il
        riferimento canonico e' `result_id`."""
        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result()],
                edges=[_edge("src-1", "r-1")],
            ),
        )
        assert result["success"] is True
        assert "output_path" not in result
        assert "output_file" not in result

    def test_output_format_from_result_node_overrides_default(self, env):
        env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result(fmt="geojson")],
                edges=[_edge("src-1", "r-1")],
            ),
        )

        # Post-refactor in-memory: geojson passa da save_dataframe_to_bytes
        # (non save_dataframe). Il format e' il secondo argomento
        # positional: save_dataframe_to_bytes(df, format, ...).
        args, kwargs = env.save_df_bytes.call_args
        assert args[1] == "geojson"


    def test_no_result_node_falls_back_to_last_node_result(self, env):
        """Senza nodo result, l'ultimo nodo con DataFrame viene usato come output."""
        env.load_proj.return_value = pd.DataFrame([{"a": 1}])

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(nodes=[_project_source()], edges=[]),
        )

        assert result["success"] is True
        assert result["rows_count"] == 1

    def test_no_result_returns_error(self, env):
        """Canvas senza nodi che producano DataFrame."""
        env.load_proj.return_value = None

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(nodes=[_project_source()], edges=[]),
        )

        assert result["success"] is False
        assert "Nessun risultato" in result["error"]


# Section 6: snapshot intermediate.db quando run_id e' presente


class TestExecuteEnrichmentIntermediateSnapshot:
    """Contract: l'arricchisci snapshot per i nodi `api_enrich`
    passa per `record_checkpoint_snapshot(module="arricchisci")` invece
    di `write_node` legacy. La policy globale
    `ARRICCHISCI_INTERMEDIATE_CHECKPOINTS=final_only` decide poi lo
    skip; il test verifica solo l'API call dell'executor."""

    def test_intermediate_snapshot_called_when_run_id_present(self, env):
        env.list_conns.return_value = {
            "api-1": {"api_type": "enricher", "connection_type": "manual"},
        }

        with patch(
            "app.core.intermediate.snapshot.record_checkpoint_snapshot"
        ) as mock_record:
            env.svc.execute_enrichment(
                "proj-1",
                _canvas(
                    nodes=[_static_values(), _api_enrich(), _result()],
                    edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
                ),
                run_id="run-xyz",
            )

        assert mock_record.called
        # Almeno una chiamata per il nodo api_enrich con module=arricchisci
        api_enrich_calls = [
            c for c in mock_record.call_args_list
            if c.kwargs.get("node_type") == "api_enrich"
            and c.kwargs.get("module") == "arricchisci"
        ]
        assert len(api_enrich_calls) >= 1

    def test_intermediate_snapshot_NOT_called_when_run_id_absent(self, env):
        env.list_conns.return_value = {
            "api-1": {"api_type": "enricher", "connection_type": "manual"},
        }

        with patch(
            "app.core.intermediate.snapshot.record_checkpoint_snapshot"
        ) as mock_record, patch(
            "app.core.intermediate.writer.write_node"
        ) as mock_write_node:
            env.svc.execute_enrichment(
                "proj-1",
                _canvas(
                    nodes=[_static_values(), _api_enrich(), _result()],
                    edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
                ),
                run_id=None,
            )

        mock_record.assert_not_called()
        mock_write_node.assert_not_called()


# Section 7: errore "API non trovata"


class TestExecuteEnrichmentResponseIncludesResultId:
    """Regression guard: il dict ritornato da execute_enrichment DEVE
    contenere `result_id` sia sul ramo DataFrame che sul ramo file_result.

    `save_result_blob` ritorna un result_id E cancella il file temp su
    success (vedi `app/core/results/result_store.py:80`). Senza `result_id` nel
    response dict, un consumer downstream che wrappa il risultato in
    `PipelineArtifact` vedrebbe `artifact_kind == "file"` e tenterebbe
    di materializzare `output_path` — un path gia' unlink-ato.
    Con `result_id` presente, `artifact_kind == "blob"` e il resolver
    legge dal DB."""

    def test_dataframe_response_contains_result_id_from_save_blob(self, env):
        # Post-refactor in-memory: csv/xlsx/... passano da save_result_blob_from_bytes
        # B3-B.9 cleanup: rimosso `env.save_blob.return_value = ...` (binding
        # api_connector alias dead post-extraction).
        env.save_blob_bytes.return_value = "res-df-42"

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result()],
                edges=[_edge("src-1", "r-1")],
            ),
        )

        assert result["success"] is True
        assert result.get("result_id") == "res-df-42"

    def test_dataframe_response_is_resolvable_as_blob_artifact(self, env):
        """End-to-end: il dict ritornato deve soddisfare il contratto
        di `PipelineArtifact.artifact_kind == 'blob'`."""
        from app.core.artifacts.types import PipelineArtifact

        # B3-B.9 cleanup: rimosso `env.save_blob.return_value = ...` (alias
        # api_connector dead post-extraction).
        env.save_blob_bytes.return_value = "res-abc"

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_project_source(), _result()],
                edges=[_edge("src-1", "r-1")],
            ),
        )

        artifact = PipelineArtifact.from_executor_result(
            result, project_id="proj-1"
        )
        assert artifact.artifact_kind == "blob"
        assert artifact.result_id == "res-abc"


class TestExecuteEnrichmentMissingApi:
    def test_api_enrich_missing_api_id_is_blocking_in_parallel_path(self, env):
        """CONTRATTO: un `api_id` sconosciuto nelle connections solleva
        `ValueError("API 'X' non trovata")` dentro `_run_single_enricher`
        → il parallel group cattura l'errore e la pipeline DEVE fallire.

        Prima dell'aprile 2026 questo errore veniva degradato a success: la
        pipeline completava con success prendendo il fallback upstream
        (static_values). Questo mascherava l'errore di config e lo
        user si trovava un output senza colonne dell'enricher ma con
        task success → silent data loss. Fixato insieme al test
        funzionale `test_backend_api_enrich_http_failure_flow`."""
        env.list_conns.return_value = {}  # api-1 NON esiste

        result = env.svc.execute_enrichment(
            "proj-1",
            _canvas(
                nodes=[_static_values(), _api_enrich(api_id="api-1"), _result()],
                edges=[_edge("sv-1", "ae-1"), _edge("ae-1", "r-1")],
            ),
        )

        assert result["success"] is False
        assert "api-1" in (result.get("error") or "").lower()
