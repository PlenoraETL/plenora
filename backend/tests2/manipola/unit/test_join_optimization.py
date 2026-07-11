from types import SimpleNamespace

import pandas as pd


def _node(node_id, op, config):
    return SimpleNamespace(id=node_id, transform_op=op, config=config, data={})


def test_projects_columns_dropped_by_immediate_single_consumer():
    from app.core.manipola.join_optimization import (
        project_columns_dropped_immediately_after_join,
    )

    join = _node("join", "join", {"left_keys": ["id"], "right_keys": ["id"]})
    drop = _node("drop", "drop_columns", {"columns": ["unused_L"]})
    canvas = SimpleNamespace(
        nodes=[join, drop],
        edges=[SimpleNamespace(source="join", target="drop")],
    )
    left = pd.DataFrame({"id": [1], "unused": ["large"], "keep": [2]})
    right = pd.DataFrame({"id": [1], "value": [3]})

    projected = project_columns_dropped_immediately_after_join(
        canvas, join, [left, right]
    )

    assert list(projected[0].columns) == ["id", "keep"]
    assert list(projected[1].columns) == ["id", "value"]
    assert "unused" in left.columns


def test_projection_is_disabled_when_join_fans_out():
    from app.core.manipola.join_optimization import (
        project_columns_dropped_immediately_after_join,
    )

    join = _node("join", "join", {"left_keys": ["id"], "right_keys": ["id"]})
    drop = _node("drop", "drop_columns", {"columns": ["unused_L"]})
    other = _node("other", "rename", {})
    canvas = SimpleNamespace(
        nodes=[join, drop, other],
        edges=[
            SimpleNamespace(source="join", target="drop"),
            SimpleNamespace(source="join", target="other"),
        ],
    )
    inputs = [pd.DataFrame({"id": [1], "unused": [2]}), pd.DataFrame({"id": [1]})]

    assert project_columns_dropped_immediately_after_join(canvas, join, inputs) is inputs


def test_compacts_only_string_payload_and_preserves_inputs():
    from app.core.manipola.join_optimization import compact_owned_join_string_columns

    join = _node("join", "join", {"left_keys": ["id"], "right_keys": ["id"]})
    left = pd.DataFrame({
        "id": pd.Series(["1", "2"], dtype=object),
        "text": pd.Series(["a", "b"], dtype=object),
        "mixed": pd.Series([1, "x"], dtype=object),
    })
    right = pd.DataFrame({"id": ["1", "2"], "value": [3, 4]})

    compacted = compact_owned_join_string_columns(
        [left, right], join, minimum_rows=1
    )

    assert getattr(compacted[0]["text"].dtype, "storage", None) == "pyarrow"
    assert compacted[0]["id"].dtype == object
    assert compacted[0]["mixed"].dtype == object
    assert left["text"].dtype == object


def test_large_null_partition_preserves_left_join_semantics(monkeypatch):
    from app.config import settings
    from app.core.manipola.transforms import joins

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 0)
    null_rows = joins._NULL_PARTITION_MIN_ROWS
    left = pd.DataFrame({
        "key": pd.Series([None] * null_rows + ["match"], dtype=object),
        "left_value": range(null_rows + 1),
    })
    right = pd.DataFrame({"other_key": ["match"], "right_value": [42]})

    result = joins.op_join(
        [left, right], left_on="key", right_on="other_key", how="left"
    )

    assert len(result) == len(left)
    assert int(result["right_value_R"].notna().sum()) == 1
    assert result.loc[result["right_value_R"].notna(), "key"].iloc[0] == "match"
    assert "other_key" not in result.columns


def test_hash_partitioned_left_join_preserves_order_and_values(monkeypatch):
    from app.config import settings
    from app.core.manipola.transforms import joins

    monkeypatch.setitem(settings.DEFAULTS, "WORKER_RSS_MAX_BYTES", 0)
    monkeypatch.setattr(joins, "_HASH_PARTITION_MIN_ROWS", 1)
    monkeypatch.setattr(joins, "plan_merge_partitions", lambda *_args, **_kwargs: 3)
    left = pd.DataFrame({"key": [3, 1, 2, 4], "value": ["c", "a", "b", "d"]})
    right = pd.DataFrame({"key": [1, 2, 3], "label": ["A", "B", "C"]})

    result = joins.op_join([left, right], left_on="key", right_on="key", how="left")

    assert result["key"].tolist() == [3, 1, 2, 4]
    assert result["label_R"].tolist()[:3] == ["C", "A", "B"]
    assert pd.isna(result["label_R"].iloc[3])
