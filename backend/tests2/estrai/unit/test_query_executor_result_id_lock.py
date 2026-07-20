"""
Lock tests per `QueryExecutor.execute_query` → `ResultBuilder.success`

→ `to_dict`.
"""

from unittest.mock import MagicMock, patch

import pytest


pytestmark = [
    pytest.mark.unit,
    pytest.mark.estrai,
    pytest.mark.contract,
    pytest.mark.regression,
]


# Contract: QueryResult dataclass


class TestQueryResultDataclass:
    def test_query_result_has_result_id_field(self):
        """Il dataclass deve esporre `result_id` per permettere ai
        caller downstream di propagarlo nel task result dict."""
        from app.core.query_execution.query_executor import QueryResult

        # Deve accettare result_id keyword (anche se Optional)
        result = QueryResult(
            success=True,
            message="ok",
            result_id="res-abc",
        )
        assert result.result_id == "res-abc"

    def test_query_result_result_id_defaults_to_none(self):
        from app.core.query_execution.query_executor import QueryResult

        result = QueryResult(success=False, message="err")
        assert result.result_id is None or result.result_id == ""


# Contract: ResultBuilder.success + to_dict


class TestResultBuilderPropagation:
    def test_success_accepts_result_id_keyword(self):
        from app.core.query_execution.query_executor import ResultBuilder

        result = ResultBuilder.success(
            rows_count=100,
            columns=["a", "b"],
            output_path="/tmp/out.csv",
            project_id="proj-1",
            timings={"total": 1.0},
            result_id="res-xyz",
        )
        assert result.result_id == "res-xyz"

    def test_to_dict_includes_result_id_on_success(self):
        from app.core.query_execution.query_executor import ResultBuilder

        result = ResultBuilder.success(
            rows_count=10,
            columns=["id"],
            output_path="/tmp/o.csv",
            project_id="proj-1",
            timings={},
            result_id="res-42",
        )
        d = ResultBuilder.to_dict(result)
        assert d["result_id"] == "res-42"

    def test_to_dict_omits_result_id_on_error(self):
        """Il dict di errore non deve esporre result_id (non esiste blob)."""
        from app.core.query_execution.query_executor import ResultBuilder

        err = ResultBuilder.error("boom")
        d = ResultBuilder.to_dict(err)
        assert d["success"] is False
        assert "result_id" not in d or d["result_id"] is None


# End-to-end: QueryExecutor.execute_query propaga result_id al dict


class TestQueryExecutorEndToEnd:
    def test_execute_query_propagates_result_id_from_save_blob(self):
        """Regression guard del bug: `save_result_blob` ritorna
        `result_id`, il flusso deve catturarlo e includerlo nel dict
        ritornato. Senza, il download via /api/tasks/{id}/download
        cade su 410 Gone perche' `task_info.result["result_id"]` e' None."""
        from app.core.query_execution.query_executor import QueryExecutor

        # Mock dell'intero flusso dipendenze
        fake_rows = [(1, "a"), (2, "b")]
        fake_columns = ["id", "name"]

        mock_engine_factory = MagicMock()
        mock_logger = MagicMock()

        executor = QueryExecutor(
            engine_factory=mock_engine_factory,
            logger=mock_logger,
        )

        # Il path DataFrame continua a propagare il result_id per i formati
        # non streamable.
        with patch.object(
            executor, "execute_raw", return_value=(fake_rows, fake_columns)
        ), patch(
            "app.core.results.streaming_blob_writer.save_dataframe_to_result_blob_streaming",
            return_value="res-generated-id",
        ) as mock_save_blob:
            conn = MagicMock()
            conn.type = "mysql"
            # NB (C11): csv/parquet/xlsx passano dal path streaming che
            # non chiama execute_raw — per testare il flow end-to-end col
            # mock di execute_raw usiamo JSON (formato non-streamable).
            # Il test verifica result_id
            # propagation che e' invariante rispetto al formato.
            result = executor.execute_query(
                connection=conn,
                sql="SELECT 1",
                project_id="proj-1",
                output_filename="out",
                output_format="json",
                geom_column=None,
                group_id="grp-1",
            )

        mock_save_blob.assert_called_once()
        assert result["success"] is True, f"expected success, got {result}"
        assert result.get("result_id") == "res-generated-id", (
            f"result_id non propagato nel dict. Dict: {result}"
        )


# PipelineArtifact interop: il dict estrai deve diventare artifact_kind=blob


class TestPipelineArtifactInterop:
    def test_estrai_result_dict_becomes_blob_kind_artifact(self):
        """End-to-end: il dict ritornato da `ResultBuilder.to_dict` con
        `result_id` valorizzato deve produrre un `PipelineArtifact` con
        `artifact_kind == "blob"` quando wrappato via
        `PipelineArtifact.from_executor_result`."""
        from app.core.query_execution.query_executor import ResultBuilder
        from app.core.artifacts.types import PipelineArtifact

        result = ResultBuilder.success(
            rows_count=5,
            columns=["a"],
            output_path="/tmp/stale.csv",  # stale dopo save_result_blob
            project_id="proj-estrai-1",
            timings={},
            result_id="res-estrai-123",
        )
        d = ResultBuilder.to_dict(result)

        artifact = PipelineArtifact.from_executor_result(d, project_id="proj-estrai-1")
        assert artifact.artifact_kind == "blob", (
            f"expected 'blob' (ha result_id), got {artifact.artifact_kind!r}. "
            f"Artifact: {dict(artifact)}"
        )
        assert artifact.result_id == "res-estrai-123"
