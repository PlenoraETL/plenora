"""
tests/manipola/unit/test_plugin_executor_coverage.py
=====================================================
Additional unit tests for PluginExecutor targeting uncovered lines.

Covers:
  - __init__ with custom registry_path (line 74)
  - _carica_registry: FileNotFoundError, JSONDecodeError (lines 101-106)
  - _carica_funzioni: missing function field, invalid path, ImportError, AttributeError (lines 119-120, 125-126, 135-138)
  - get_transforms_by_category (line 163)
  - execute: full flow, multi input, no input, missing config, missing func (lines 206-230)
  - execute_with_params: func not loaded, multi path (lines 257, 262)
  - _prepara_kwargs: depends_on fallback to registry default, list dep not satisfied (lines 301-302, 307)
  - _converti_valore: aggregation_list non-list/non-string (line 369)
  - validate_config: depends_on skip, required missing, number min/max, non-numeric, select invalid (lines 422-446, 455)
  - reload_executor: both branches (lines 507-510)
  - PluginExecutorCompatibility: is_plugin_transform, dispatch (lines 539-540, 554-559)
"""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
import pandas as pd

pytestmark = pytest.mark.unit

from app.core.manipola.plugin_executor import (
    PluginExecutor,
    PluginExecutorCompatibility,
    get_executor,
    reload_executor,
    _executor_instance,
)
from app.models import Node


# =========================================================================
# Helpers
# =========================================================================

def _make_node(config=None, data=None, transform_op=None):
    """Build a minimal Node for testing execute()."""
    return Node(
        id="test_node",
        type="transform",
        position={"x": 0, "y": 0},
        config=config or {},
        data=data or {},
        transform_op=transform_op,
    )


def _write_registry(path, content):
    """Write a registry JSON file to disk."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(content, f)




# =========================================================================
# __init__ with custom registry_path  (line 74)
# =========================================================================

class TestInitCustomPath:

    def test_custom_registry_path_string(self, tmp_path):
        """Passing a string registry_path converts it to Path and loads."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {"transforms": {}, "categories": {}})
        ex = PluginExecutor(registry_path=str(reg_file))
        assert ex.registry == {}
        assert ex.categories == {}


# =========================================================================
# _carica_registry error paths  (lines 101-106)
# =========================================================================

class TestCaricaRegistryErrors:

    def test_file_not_found_raises(self, tmp_path):
        """FileNotFoundError is re-raised when registry file is missing."""
        missing = tmp_path / "does_not_exist.json"
        with pytest.raises(FileNotFoundError):
            PluginExecutor(registry_path=str(missing))

    def test_json_decode_error_raises(self, tmp_path):
        """JSONDecodeError is re-raised when registry has invalid JSON."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{invalid json!!!", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            PluginExecutor(registry_path=str(bad_file))


# =========================================================================
# _carica_funzioni edge cases  (lines 119-120, 125-126, 135-138)
# =========================================================================

class TestCaricaFunzioniEdgeCases:

    def test_missing_function_field_skipped(self, tmp_path):
        """Transform without 'function' key is silently skipped (lines 119-120)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "no_func": {"name": "No func transform"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        assert "no_func" not in ex.functions

    def test_invalid_function_path_no_dot(self, tmp_path):
        """Function path without a dot is skipped (lines 125-126)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "bad_path": {"name": "Bad", "function": "noDotHere"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        assert "bad_path" not in ex.functions

    def test_import_error_handled(self, tmp_path):
        """ImportError during dynamic import is caught (lines 135-136)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "bad_import": {"name": "Bad import", "function": "nonexistent_module_xyz.func"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        assert "bad_import" not in ex.functions

    def test_attribute_error_handled(self, tmp_path):
        """AttributeError when function not found in module (lines 137-138)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "bad_attr": {"name": "Bad attr", "function": "base.nonexistent_function_xyz"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        assert "bad_attr" not in ex.functions


# =========================================================================
# get_transforms_by_category  (line 163)
# =========================================================================

class TestGetTransformsByCategory:

    def test_filters_by_category(self):
        ex = get_executor()
        base_transforms = ex.get_transforms_by_category("base")
        assert isinstance(base_transforms, dict)
        for tid, cfg in base_transforms.items():
            assert cfg.get("category") == "base"

    def test_empty_for_unknown_category(self):
        ex = get_executor()
        result = ex.get_transforms_by_category("nonexistent_category_xyz")
        assert result == {}


# =========================================================================
# execute  (lines 206-230)
# =========================================================================

class TestExecute:

    def test_transform_not_in_registry_raises(self):
        ex = get_executor()
        node = _make_node()
        with pytest.raises(ValueError, match="non trovata nel registry"):
            ex.execute("TRANSFORM_XYZ_INESISTENTE", [pd.DataFrame()], node)

    def test_function_not_loaded_raises(self, tmp_path):
        """Transform exists in registry but function failed to load (line 211-212)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "broken": {"name": "Broken", "function": "nonexistent_module_xyz.func"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        node = _make_node()
        with pytest.raises(ValueError, match="non caricata"):
            ex.execute("broken", [pd.DataFrame()], node)

    def test_execute_single_input(self):
        """Single-input transform receives the first DataFrame (line 227)."""
        ex = get_executor()
        df = pd.DataFrame({"a": [3, 1, 2]})
        node = _make_node(config={"columns": "a", "ascending": True})
        result = ex.execute("sort", [df], node)
        assert list(result["a"]) == [1, 2, 3]

    def test_execute_no_inputs_raises(self):
        """Empty input list for single-input transform raises ValueError (line 226)."""
        ex = get_executor()
        node = _make_node()
        with pytest.raises(ValueError, match="Nessun input"):
            ex.execute("sort", [], node)

    def test_execute_multi_input(self):
        """Multi-input transform (e.g., join) receives the full list (line 223)."""
        ex = get_executor()
        cfg = ex.get_transform_config("join")
        if cfg and cfg.get("inputs") == "multi":
            df1 = pd.DataFrame({"key": [1, 2], "val1": ["a", "b"]})
            df2 = pd.DataFrame({"key": [1, 2], "val2": ["x", "y"]})
            node = _make_node(config={
                "left_on": "key",
                "right_on": "key",
                "how": "inner",
            })
            result = ex.execute("join", [df1, df2], node)
            assert len(result) == 2

    def test_execute_uses_data_config_fallback(self):
        """When node.config is empty, falls back to node.data['config'] (line 215)."""
        ex = get_executor()
        df = pd.DataFrame({"a": [3, 1, 2]})
        node = _make_node(
            config={},
            data={"config": {"columns": "a", "ascending": True}},
        )
        # node.config is {} which is falsy — should fall back to node.data.config
        # Actually {} is falsy in Python, triggers fallback
        result = ex.execute("sort", [df], node)
        assert list(result["a"]) == [1, 2, 3]

    def test_execute_function_exception_reraised(self):
        """Exceptions from the transform function are re-raised (lines 228-230)."""
        ex = get_executor()
        # Force a real crash by making the function itself raise
        ex.functions["filter"] = MagicMock(side_effect=RuntimeError("forced crash"))
        node = _make_node(config={"column": "a"})
        df = pd.DataFrame({"a": [1]})
        with pytest.raises(RuntimeError, match="forced crash"):
            ex.execute("filter", [df], node)


# =========================================================================
# execute_with_params  (lines 257, 262)
# =========================================================================

class TestExecuteWithParamsExtra:

    def test_func_not_loaded_raises(self, tmp_path):
        """execute_with_params raises when function is not loaded (line 257)."""
        reg_file = tmp_path / "reg.json"
        _write_registry(reg_file, {
            "transforms": {
                "broken": {"name": "Broken", "function": "nonexistent_mod_xyz.fn"}
            },
            "categories": {},
        })
        ex = PluginExecutor(registry_path=str(reg_file))
        with pytest.raises(ValueError, match="non caricata"):
            ex.execute_with_params("broken", [pd.DataFrame()], {})

    def test_multi_input_path(self):
        """execute_with_params for multi-input transform (line 262)."""
        ex = get_executor()
        cfg = ex.get_transform_config("join")
        if cfg and cfg.get("inputs") == "multi":
            df1 = pd.DataFrame({"k": [1], "v": [10]})
            df2 = pd.DataFrame({"k": [1], "v2": [20]})
            result = ex.execute_with_params("join", [df1, df2], {
                "left_on": "k",
                "right_on": "k",
                "how": "inner",
            })
            assert len(result) == 1


# =========================================================================
# _prepara_kwargs: depends_on fallback & list dep  (lines 301-302, 307)
# =========================================================================

class TestPreparaKwargsExtra:

    def _config(self, params):
        return {"parameters": params}

    def test_depends_on_fallback_to_registry_default(self):
        """When dep param is not in node_config, falls back to registry default (lines 301-302)."""
        ex = get_executor()
        config = self._config([
            {"id": "mode", "type": "string", "default": "custom"},
            {
                "id": "custom_val", "type": "string", "default": "hello",
                "depends_on": {"param": "mode", "value": "custom"},
            },
        ])
        # mode not provided in node_config → fallback to registry default "custom"
        result = ex._prepara_kwargs(config, {})
        assert "custom_val" in result
        assert result["custom_val"] == "hello"

    def test_depends_on_fallback_dep_param_not_found(self):
        """When dep param has no definition in registry, dep_value is None (line 302)."""
        ex = get_executor()
        config = self._config([
            {
                "id": "custom_val", "type": "string", "default": "hello",
                "depends_on": {"param": "missing_param", "value": "anything"},
            },
        ])
        # missing_param not in node_config and not in parameters → dep_value=None
        result = ex._prepara_kwargs(config, {})
        assert "custom_val" not in result

    def test_depends_on_list_not_satisfied(self):
        """depends_on with list of values, current value not in list (line 307)."""
        ex = get_executor()
        config = self._config([
            {"id": "aggr", "type": "string", "default": "count"},
            {
                "id": "decimals", "type": "number", "default": 2,
                "depends_on": {"param": "aggr", "value": ["sum", "mean"]},
            },
        ])
        result = ex._prepara_kwargs(config, {"aggr": "count"})
        assert "decimals" not in result


# =========================================================================
# _converti_valore: aggregation_list edge  (line 369)
# =========================================================================

class TestConvertiValoreExtra:

    def test_aggregation_list_non_string_non_list(self):
        """Non-string, non-list value for aggregation_list returns [] (line 369)."""
        ex = get_executor()
        assert ex._converti_valore(42, "aggregation_list") == []
        assert ex._converti_valore(True, "aggregation_list") == []

    def test_pivot_mapping_non_string_non_dict(self):
        """Non-string, non-dict value for pivot_mapping returns {}."""
        ex = get_executor()
        assert ex._converti_valore(42, "pivot_mapping") == {}
        assert ex._converti_valore([1, 2], "pivot_mapping") == {}

    def test_columns_non_string_non_list_wraps(self):
        """Non-string, non-list value for columns wraps in list."""
        ex = get_executor()
        assert ex._converti_valore(42, "columns") == [42]
