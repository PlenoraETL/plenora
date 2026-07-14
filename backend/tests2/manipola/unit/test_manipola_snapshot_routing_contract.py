"""
Contract test Current contract

Verifica che il loop trasformazioni di MANIPOLA passa per la nuova
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from app.core.manipola.executor import ManipolaExecutor


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _node(node_id: str, ntype: str, **attrs):
    payload = {
        "id": node_id,
        "type": ntype,
        "data": attrs.pop("data", None),
        "transform_op": attrs.pop("transform_op", None),
    }
    payload.update(attrs)
    return SimpleNamespace(**payload)


def _edge(src: str, tgt: str):
    return SimpleNamespace(source=src, target=tgt)


def _canvas(nodes, edges):
    return SimpleNamespace(nodes=nodes, edges=edges)


def _patch_settings(**overrides):
    """Sovrascrive le settings policy per il test."""
    from app.core.intermediate import policy as _policy
    from app.core.intermediate.policy import reset_validation_cache

    cfg = {
        "mode": "metadata_only",
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


def _executor() -> ManipolaExecutor:
    """Executor con project_service stub."""
    project_service = MagicMock()
    project_service.get_project_output_contract.return_value = None
    project_service.update_project_output_contract.return_value = True
    return ManipolaExecutor(project_service=project_service)


def _df(n: int = 5) -> pd.DataFrame:
    return pd.DataFrame({"a": list(range(n)), "b": list(range(n))})


# A. Loop trasformazioni: routing snapshot per ogni nodo


def test_transform_loop_calls_record_checkpoint_for_each_non_source_node():
    """Il loop chiama `record_checkpoint_snapshot` per ogni nodo
    non-source (transform / multi_join), passando module="manipola"
    + transform_id = node.transform_op se transform."""
    executor = _executor()

    src = _node("src", "project_source")
    t_join = _node("t1", "transform", transform_op="join")
    t_rename = _node("t2", "transform", transform_op="rename")
    mj = _node("mj", "multi_join")
    res = _node("res", "result")
    canvas = _canvas(
        [src, t_join, t_rename, mj, res],
        [
            _edge("src", "t1"),
            _edge("t1", "t2"),
            _edge("t2", "mj"),
            _edge("src", "mj"),
            _edge("mj", "res"),
        ],
    )
    dataframes = {"src": _df(10)}

    # Mock le esecuzioni transform/multi_join: ritornano un nuovo DataFrame.
    monkey_df = _df(20)
    fake_record = MagicMock()
    fake_record.return_value = MagicMock(recorded=True)

    with patch.object(
        executor, "_esegui_trasformazione", return_value=monkey_df,
    ), patch.object(
        executor, "_esegui_multi_join", return_value=monkey_df,
    ), patch(
        "app.core.manipola.executor.record_checkpoint_snapshot",
        fake_record,
    ), _patch_settings():
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    # 3 chiamate: t_join, t_rename, mj. Result NON e' nel loop _applica_trasformazioni.
    assert fake_record.call_count == 3

    calls_by_node = {c.kwargs["node_id"]: c for c in fake_record.call_args_list}
    assert set(calls_by_node) == {"t1", "t2", "mj"}

    # transform_id propagato solo per node.type=transform
    assert calls_by_node["t1"].kwargs["transform_id"] == "join"
    assert calls_by_node["t2"].kwargs["transform_id"] == "rename"
    assert calls_by_node["mj"].kwargs["transform_id"] is None

    # module sempre manipola
    for c in fake_record.call_args_list:
        assert c.kwargs["module"] == "manipola"


def test_transform_loop_does_not_call_legacy_write_node():
    """Anti-regression: il loop NON deve piu' chiamare
    `intermediate.writer.write_node` con `df.to_dict(orient="records")`."""
    executor = _executor()
    src = _node("src", "project_source")
    t = _node("t", "transform", transform_op="rename")
    canvas = _canvas([src, t], [_edge("src", "t")])
    dataframes = {"src": _df()}

    write_node_spy = MagicMock(side_effect=AssertionError(
        "write_node legacy NON deve essere chiamato dal loop manipola"
    ))

    with patch.object(
        executor, "_esegui_trasformazione", return_value=_df(),
    ), patch(
        "app.core.intermediate.writer.write_node", write_node_spy,
    ), _patch_settings():
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    write_node_spy.assert_not_called()


# B. Skip path: noncritical transform -> no df.to_dict, no DB


def test_noncritical_transform_does_not_materialize_dataframe():
    """Policy default `critical`: un transform `rename` (noncritical) non
    deve mai chiamare to_dict ne' touchare il DB. Il check avviene dentro
    record_checkpoint_snapshot via resolve_intermediate_policy."""
    executor = _executor()
    src = _node("src", "project_source")
    t_rename = _node("t", "transform", transform_op="rename")
    canvas = _canvas([src, t_rename], [_edge("src", "t")])
    dataframes = {"src": _df(10)}

    # DataFrame con to_dict spy: se viene chiamato, fail.
    materialized = pd.DataFrame({"x": [1, 2, 3]})
    spy_df = MagicMock(wraps=materialized)
    spy_df.to_dict = MagicMock(side_effect=AssertionError(
        "to_dict NON deve essere chiamato per nodo noncritical"
    ))
    spy_df.head = MagicMock(side_effect=AssertionError(
        "head NON deve essere chiamato per nodo noncritical"
    ))
    # Difensivo: hash_pandas_object usa .columns / .__getitem__ — questi
    # restano wrappati (MagicMock(wraps=...) delega ai metodi originali).

    db_spy = MagicMock(side_effect=AssertionError(
        "write_node_snapshot_atomic NON deve essere chiamato per noncritical"
    ))

    with patch.object(
        executor, "_esegui_trasformazione", return_value=spy_df,
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic", db_spy,
    ), _patch_settings(manipola_checkpoints="critical"):
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    db_spy.assert_not_called()


def test_critical_transform_triggers_db_write():
    """Counterpart del precedente: un transform `join` (critical)
    arriva fino al DB write con preview e hash."""
    executor = _executor()
    src = _node("src", "project_source")
    t_join = _node("t", "transform", transform_op="join")
    canvas = _canvas([src, t_join], [_edge("src", "t")])
    dataframes = {"src": _df()}

    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)

    with patch.object(
        executor, "_esegui_trasformazione", return_value=_df(20),
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic",
        side_effect=_capture,
    ), _patch_settings(manipola_checkpoints="critical"):
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    assert captured["node_id"] == "t"
    assert captured["row_count"] == 20
    assert captured["persistence_mode"] == "metadata_only"
    assert captured["rows_to_persist"] == []  # metadata-only: no PG payload
    assert captured["node_hash"] is not None


def test_multi_join_always_checkpointed():
    """multi_join e' sempre critical, indipendentemente dalla policy."""
    executor = _executor()
    src1 = _node("s1", "project_source")
    src2 = _node("s2", "project_source")
    mj = _node("mj", "multi_join")
    canvas = _canvas(
        [src1, src2, mj],
        [_edge("s1", "mj"), _edge("s2", "mj")],
    )
    dataframes = {"s1": _df(), "s2": _df()}

    db_spy = MagicMock()

    with patch.object(
        executor, "_esegui_multi_join", return_value=_df(30),
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic", db_spy,
    ), _patch_settings(manipola_checkpoints="critical"):
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    assert db_spy.called
    kwargs = db_spy.call_args.kwargs
    assert kwargs["node_id"] == "mj"


# C. Policy matrix


def test_policy_none_skips_all_checkpoint_transforms():
    """`MANIPOLA_INTERMEDIATE_CHECKPOINTS=none`: nessun transform/multi_join
    raggiunge il DB write. Result e' gestito separatamente da
    record_final_snapshot e non passa per il loop checkpoint."""
    executor = _executor()
    src = _node("src", "project_source")
    t_join = _node("t1", "transform", transform_op="join")
    t_filter = _node("t2", "transform", transform_op="filter")
    mj = _node("mj", "multi_join")
    canvas = _canvas(
        [src, t_join, t_filter, mj],
        [_edge("src", "t1"), _edge("t1", "t2"), _edge("t2", "mj"), _edge("src", "mj")],
    )
    dataframes = {"src": _df()}

    db_spy = MagicMock(side_effect=AssertionError(
        "Nessun checkpoint deve raggiungere il DB con policy=none"
    ))

    with patch.object(
        executor, "_esegui_trasformazione", return_value=_df(),
    ), patch.object(
        executor, "_esegui_multi_join", return_value=_df(),
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic", db_spy,
    ), _patch_settings(manipola_checkpoints="none"):
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    db_spy.assert_not_called()


def test_policy_all_records_every_transform_including_noncritical():
    executor = _executor()
    src = _node("src", "project_source")
    t_rename = _node("t1", "transform", transform_op="rename")  # noncritical
    t_pad = _node("t2", "transform", transform_op="string_pad")  # noncritical
    canvas = _canvas(
        [src, t_rename, t_pad],
        [_edge("src", "t1"), _edge("t1", "t2")],
    )
    dataframes = {"src": _df()}

    db_spy = MagicMock()

    with patch.object(
        executor, "_esegui_trasformazione", return_value=_df(),
    ), patch(
        "app.core.intermediate.node_snapshots_db.write_node_snapshot_atomic", db_spy,
    ), _patch_settings(manipola_checkpoints="all"):
        executor._applica_trasformazioni(canvas, dataframes, run_id="run-1")

    assert db_spy.call_count == 2
    persisted_ids = {c.kwargs["node_id"] for c in db_spy.call_args_list}
    assert persisted_ids == {"t1", "t2"}


# D. Result via record_final_snapshot


def test_result_node_uses_record_final_snapshot():
    """Il path result chiama `record_final_snapshot` con
    module='manipola' e node_type='result'."""

    executor = _executor()
    final_spy = MagicMock(return_value=MagicMock(recorded=True))

    src = _node("src", "project_source")
    res = _node("r", "result")
    canvas = _canvas([src, res], [_edge("src", "r")])

    # Mock dei 4 step pre-snapshot result:
    #   _carica_sorgenti_progetto / _file / _statiche / _applica_trasformazioni
    # poi _applica_group_by passthrough, poi _salva_risultato success.
    df = _df(100)

    def _populate_dataframes(_canvas, dfs, force_recalculate=False, **_kwargs):
        dfs["src"] = df

    with patch.object(executor, "_init_security_context"), patch.object(
        executor, "_carica_sorgenti_progetto", side_effect=_populate_dataframes,
    ), patch.object(
        executor, "_carica_sorgenti_file", return_value=None,
    ), patch.object(
        executor, "_carica_sorgenti_statiche", return_value=None,
    ), patch.object(
        executor, "_applica_trasformazioni", return_value=df,
    ), patch.object(
        executor, "_applica_group_by", side_effect=lambda d, _node: d,
    ), patch.object(
        executor, "_salva_risultato", return_value={"success": True, "rows_count": 100},
    ), patch(
        "app.core.manipola.executor.record_final_snapshot", final_spy,
    ), _patch_settings():
        executor.execute("proj-1", canvas, run_id="run-1")

    assert final_spy.called
    kwargs = final_spy.call_args.kwargs
    assert kwargs["module"] == "manipola"
    assert kwargs["node_type"] == "result"
    assert kwargs["node_id"] == "r"
    assert kwargs["run_id"] == "run-1"
