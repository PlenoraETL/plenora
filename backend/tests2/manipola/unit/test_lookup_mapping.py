"""Regressioni lookup: formato UI, compatibilita' legacy e tipi nuovi."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.core.manipola.transforms.analysis import op_lookup
from app.core.projects.schema_transform_handlers import TransformSchemaHandlers
from app.shared.execution.schema_types import ColumnSchema

pytestmark = [pytest.mark.unit, pytest.mark.manipola]


@pytest.mark.parametrize("mapping", [
    {"a": "A"}, [{"old_name": "a", "old_name_id": "ignored", "new_name": "A"}],
    [{"from": "a", "to": "A"}],
])
def test_lookup_accepts_ui_list_regression(mapping):
    # Prima del fix Series.map riceveva una lista e sollevava TypeError.
    df = pd.DataFrame({"source": ["a", "b"]})
    result = op_lookup(df, "source", mapping, output_column="out")
    assert result["out"].tolist() == ["A", "b"]
    assert list(df) == ["source"]


@pytest.mark.parametrize("kwargs, expected", [
    ({}, ["10", "b"]), ({"default": "20"}, ["10", "20"]),
    ({"unmatched": "keep", "default": "ignored"}, ["text", "b"]),
    ({"unmatched": "value", "default": "fallback"}, ["text", "fallback"]),
])
def test_legacy_and_explicit_unmatched(kwargs, expected):
    mapped = "text" if "unmatched" in kwargs else "10"
    result = op_lookup(pd.DataFrame({"s": ["a", "b"]}), "s", {"a": mapped}, **kwargs)
    assert result.s.tolist() == expected


@pytest.mark.parametrize("mode", ["keep", "null", "value"])
def test_numeric_cast_decimal_empty_and_default(mode):
    result = op_lookup(pd.DataFrame({"s": ["a", "b", "c", "9"]}), "s",
                       {"a": "1,5", "b": "", "c": None}, unmatched=mode, default="2,5")
    if mode == "keep":
        assert result.s.iloc[[0, 1, 3]].tolist() == ["1,5", "", "9"]
        assert pd.isna(result.s.iloc[2])
        return
    assert pd.api.types.is_numeric_dtype(result.s)
    assert result.s.iloc[0] == 1.5
    assert result.s.iloc[1:3].isna().all()
    if mode == "null":
        assert pd.isna(result.s.iloc[3])
    else:
        assert result.s.iloc[3] == (9 if mode == "keep" else 2.5)


def test_explicit_null_does_not_restore_mapped_null():
    df = pd.DataFrame({"s": ["a", "b", "c"]})
    result = op_lookup(df, "s", {"a": None, "b": "text"}, unmatched="keep")
    assert pd.isna(result.s.iloc[0])
    assert result.s.iloc[1:].tolist() == ["text", "c"]
    assert op_lookup(df, "s", {"a": None}).s.tolist() == df.s.tolist()
    assert op_lookup(df, "s", {}) is df
    assert op_lookup(df, "s", None) is df


@pytest.mark.parametrize("mode", ["keep", "null", "value"])
@pytest.mark.parametrize("target", [None, "out", "existing"])
def test_lookup_schema_numeric_matches_runtime(mode, target):
    config = {"column": "s", "mapping": [{"old_name": "a", "new_name": "1,5"}], "unmatched": mode, "default": "2", "output_column": target}
    handler = TransformSchemaHandlers(ColumnSchema).build_transform_handlers({})["lookup"]
    schema = handler([[ColumnSchema("s", "str"), ColumnSchema("existing", "str")]], config, "n")
    assert next(c.type for c in schema if c.name == (target or "s")) == ("str" if mode == "keep" else "real")
    config.pop("unmatched")
    schema = handler([[ColumnSchema("s", "str"), ColumnSchema("existing", "str")]], config, "n")
    assert next(c.type for c in schema if c.name == (target or "s")) == "str"


def test_registry_does_not_inject_unmatched_into_legacy_config():
    from app.core.manipola.plugin_executor import PluginExecutor
    executor = PluginExecutor()
    registry = json.loads((Path(__file__).parents[3] / "app/core/manipola/transforms_registry.json").read_text(encoding="utf-8"))["transforms"]["lookup"]
    params = {p["id"]: p for p in registry["parameters"]}
    assert "unmatched" not in executor._prepara_kwargs(registry, {"column": "s", "mapping": {"a": "1"}})
    assert "default" not in params["unmatched"]
    assert {o["value"] for o in params["unmatched"]["options"]} == {"keep", "null", "value"}


def test_invalid_unmatched_rejected():
    with pytest.raises(ValueError, match="unmatched"):
        op_lookup(pd.DataFrame({"s": ["a"]}), "s", {"a": "text"}, unmatched="invalid")


def test_empty_mapping_new_config_honors_unmatched():
    df = pd.DataFrame({"s": ["a", "b"]})
    assert op_lookup(df, "s", {}, unmatched="null").s.isna().all()
    assert op_lookup(df, "s", [], unmatched="value", default="3,5").s.tolist() == [3.5, 3.5]


@pytest.mark.parametrize("mapping", [[{"old_name": "a"}], "invalid"])
def test_invalid_mapping_rejected(mapping):
    with pytest.raises(ValueError, match="Lookup"):
        op_lookup(pd.DataFrame({"s": ["a"]}), "s", mapping)
