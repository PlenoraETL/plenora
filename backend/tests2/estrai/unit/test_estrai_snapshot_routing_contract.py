"""
Contract test Current contract

Verifica che `QueryExecutor.execute_query` salvi il final snapshot
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.db.query_result_persistence import PersistedQueryResult


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _patch_settings(**overrides):
    """Sovrascrive le settings policy per il test."""
    from app.core.intermediate import policy as _policy
    from app.core.intermediate.policy import reset_validation_cache

    cfg = {
        "mode": "preview_only",
        "preview_rows": 500,
        "artifact_format": "parquet",
        "manipola_checkpoints": "critical",
        "estrai_checkpoints": "final_only",
        "arricchisci_checkpoints": "final_only",
        "artifact_bucket": None,
        "artifact_prefix": "intermediate",
        "fingerprint_mode": "sample",
        "fingerprint_sample_rows": 1000,
    }
    cfg.update(overrides)
    reset_validation_cache()
    return patch.object(_policy, "_read_settings", return_value=cfg)


@pytest.fixture(autouse=True)
def _reset_policy_cache():
    from app.core.intermediate.policy import reset_validation_cache
    reset_validation_cache()
    yield
    reset_validation_cache()


def _build_executor():
    from app.db.query_executor import QueryExecutor

    factory = MagicMock()
    return QueryExecutor(engine_factory=factory, logger=MagicMock())


def _stub_save(monkeypatch):
    """Mock query result persistence per evitare FS I/O."""
    fake_save = MagicMock(return_value=PersistedQueryResult(
        result_id="rid-1",
        output_path="out.csv",
    ))
    monkeypatch.setattr(
        "app.db.query_result_persistence.QueryResultPersistence.persist_dataframe_result",
        fake_save,
    )
    return fake_save


# A. df NON None -> record_final_snapshot via policy


def test_estrai_final_snapshot_uses_record_final_snapshot(monkeypatch):
    """`execute_query` con df disponibile chiama
    `record_final_snapshot(module="estrai", node_type="estrai")`.
    Il legacy `write_node` non deve essere chiamato sul percorso
    df-presente."""
    executor = _build_executor()
    _stub_save(monkeypatch)

    # Spy / fake della pipeline pre-snapshot.
    rows_data = [(i, f"v{i}") for i in range(50)]
    columns_data = ["id", "name"]
    monkeypatch.setattr(
        executor, "execute_raw",
        MagicMock(return_value=(rows_data, columns_data)),
    )

    final_spy = MagicMock(return_value=MagicMock(recorded=True))
    write_node_spy = MagicMock(side_effect=AssertionError(
        "write_node legacy NON deve essere chiamato sul path df!=None"
    ))

    fake_conn = MagicMock()
    fake_conn.type = "postgres"

    with patch(
        "app.core.intermediate.snapshot.record_final_snapshot", final_spy,
    ), patch(
        "app.core.intermediate.writer.write_node", write_node_spy,
    ), _patch_settings(mode="preview_only"):
        executor.execute_query(
            connection=fake_conn,
            sql="SELECT 1",
            project_id="proj",
            output_filename="out",
            output_format="json",  # non-streamable -> df path
            run_id="run-1",
            node_id="n-estrai",
        )

    final_spy.assert_called_once()
    kwargs = final_spy.call_args.kwargs
    assert kwargs["run_id"] == "run-1"
    assert kwargs["module"] == "estrai"
    assert kwargs["node_id"] == "n-estrai"
    assert kwargs["node_type"] == "estrai"
    assert isinstance(kwargs["df"], pd.DataFrame)
    assert len(kwargs["df"]) == 50
    write_node_spy.assert_not_called()


# B. policy metadata_only -> 0 node_rows ma hash + meta presenti


def test_estrai_metadata_only_writes_meta_and_hash_no_rows(monkeypatch):
    """Con `INTERMEDIATE_PERSISTENCE_MODE=metadata_only`, il DB UPSERT
    e' chiamato con rows_to_persist=[] ma row_count e node_hash reali."""
    executor = _build_executor()
    _stub_save(monkeypatch)

    rows_data = [(i, f"v{i}") for i in range(2000)]
    columns_data = ["id", "name"]
    monkeypatch.setattr(
        executor, "execute_raw",
        MagicMock(return_value=(rows_data, columns_data)),
    )

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    fake_conn = MagicMock()
    fake_conn.type = "postgres"

    with patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture,
    ), _patch_settings(mode="metadata_only"):
        executor.execute_query(
            connection=fake_conn,
            sql="SELECT 1",
            project_id="proj",
            output_filename="out",
            output_format="json",
            run_id="run-2",
            node_id="n-estrai",
        )

    assert captured["row_count"] == 2000
    assert captured["rows_to_persist"] == []
    assert captured["persistence_mode"] == "metadata_only"
    assert captured["node_hash"] is not None
    # FIXME: #24: default fingerprint mode = sample.
    assert captured["hash_algo"] == "pandas_sample_hash_v1"


# C. policy preview_only sopra cap legacy non usa logica custom


def test_estrai_preview_only_above_legacy_cap_caps_to_preview_rows(monkeypatch):
    """Previous contract 3A: sopra `INTERMEDIATE_SNAPSHOT_MAX_ROWS` (default 100k)
    ESTRAI saltava le righe scrivendo `status=ok_sampled` con rows=[].
    Current contract 3A: la policy centrale (`preview_only` + 500) decide,
    indipendentemente dal cap legacy. Verifica che il DB write riceva
    esattamente 500 righe e row_count reale."""
    executor = _build_executor()
    _stub_save(monkeypatch)

    rows_data = [(i,) for i in range(5_000)]  # > preview_rows, < ESTRAI_MAX_ROWS
    columns_data = ["id"]
    monkeypatch.setattr(
        executor, "execute_raw",
        MagicMock(return_value=(rows_data, columns_data)),
    )

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    fake_conn = MagicMock()
    fake_conn.type = "postgres"

    # mode=preview_only + preview_rows=500
    with patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture,
    ), _patch_settings(mode="preview_only", preview_rows=500):
        executor.execute_query(
            connection=fake_conn,
            sql="SELECT 1",
            project_id="proj",
            output_filename="out",
            output_format="json",
            run_id="run-3",
            node_id="n-estrai",
        )

    assert captured["row_count"] == 5_000
    assert len(captured["rows_to_persist"]) == 500
    assert captured["persistence_mode"] == "preview_only"
    assert captured["preview_truncated"] is True


# D. Streaming branch (df=None) preserva legacy ok_streamed


def test_estrai_streaming_branch_uses_legacy_write_node_ok_streamed(monkeypatch):
    """Quando il path streaming attiva df=None (CSV/Parquet senza geom),
    il fingerprint sul DataFrame non e' calcolabile: conserviamo lo
    status legacy `ok_streamed` via write_node legacy con rows=[]."""
    executor = _build_executor()

    monkeypatch.setattr(
        executor._result_persistence,
        "persist_streamed_file",
        MagicMock(return_value=PersistedQueryResult(
            result_id="rid-stream",
            output_path="out.csv",
        )),
    )

    monkeypatch.setattr(
        executor, "_execute_streaming_to_file",
        MagicMock(return_value=(123, ["col1", "col2"])),
    )

    write_node_spy = MagicMock()
    final_spy = MagicMock(side_effect=AssertionError(
        "record_final_snapshot NON deve essere chiamato sul path streaming"
    ))

    fake_conn = MagicMock()
    fake_conn.type = "postgres"

    with patch(
        "app.core.intermediate.writer.write_node", write_node_spy,
    ), patch(
        "app.core.intermediate.snapshot.record_final_snapshot", final_spy,
    ), _patch_settings(mode="preview_only"):
        executor.execute_query(
            connection=fake_conn,
            sql="SELECT *",
            project_id="proj",
            output_filename="out",
            output_format="csv",  # streamable + no geom -> df=None
            run_id="run-4",
            node_id="n-estrai",
        )

    final_spy.assert_not_called()
    write_node_spy.assert_called_once()
    kwargs = write_node_spy.call_args.kwargs
    assert kwargs["status"] == "ok_streamed"
    assert kwargs["rows"] == []
