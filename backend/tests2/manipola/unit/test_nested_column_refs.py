"""Ogni riferimento nested condiviso deve essere risolto anche al runtime."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.core.manipola.plugin_executor import PluginExecutor
from app.shared.execution.config_column_refs import (
    _NESTED_REF_KEYS, referenced_column_names, resolve_config_column_refs,
)

pytestmark = [pytest.mark.unit, pytest.mark.manipola, pytest.mark.contract]


@pytest.mark.parametrize("key, fields", list(_NESTED_REF_KEYS.items()))
def test_every_shared_nested_reference_is_resolved_at_runtime(key, fields):
    name, column_id = fields
    config = {key: [{name: "stale", column_id: "stable"}, {name: "fallback", column_id: "missing"}, {name: "legacy"}, "unchanged"]}
    before = deepcopy(config)
    node = SimpleNamespace(data={"inputColumns": [{"id": "stable", "name": "current"}]})
    result = PluginExecutor()._resolve_column_references(config, node)
    assert result == resolve_config_column_refs(config, {"stable": "current"})
    assert result[key][0][name] == "current"
    assert result[key][1][name] == "fallback"
    assert result[key][2][name] == "legacy"
    assert referenced_column_names(result) >= {"current", "fallback", "legacy"}
    assert config == before


def test_formula_items_and_plain_columns_are_not_rewritten():
    config = {"items": [{"new_column": "x", "formula": "old + 1"}], "columns": ["old"], "column": "old", "column_column_id": "stable"}
    node = SimpleNamespace(data={"inputColumns": [{"id": "stable", "name": "current"}]})
    result = PluginExecutor()._resolve_column_references(config, node)
    assert result["items"] == config["items"]
    assert result["columns"] == ["old"]
    assert result["column"] == "current"


def test_conditions_and_selection_execute_with_renamed_upstream_column():
    import pandas as pd
    from app.core.manipola.transforms.filtering import op_filter
    from app.core.manipola.transforms.columns import op_select_columns
    executor = PluginExecutor()
    node = SimpleNamespace(data={"inputColumns": [{"id": "stable", "name": "current"}]})
    config = executor._resolve_column_references({"conditions": [{"column": "stale", "column_id": "stable", "operator": ">", "value": "1"}]}, node)
    df = pd.DataFrame({"current": [1, 2], "other": [3, 4]})
    out = op_filter(df, **config)
    assert out["current"].tolist() == [2]
    config = executor._resolve_column_references({"columns": [{"name": "stale", "name_column_id": "stable", "label": "renamed"}]}, node)
    assert list(op_select_columns(out, **config)) == ["renamed", "other"]
