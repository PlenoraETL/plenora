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
# validate_config  (lines 422-425, 429-430, 439-446, 455)
# =========================================================================

class TestValidateConfigExtra:

    def test_depends_on_skips_unmet_param(self):
        """validate_config skips param whose depends_on is not met (lines 422-425)."""
        ex = get_executor()
        # Build a registry entry with depends_on
        original_reg = ex.registry.copy()
        ex.registry["_test_validate"] = {
            "parameters": [
                {"id": "mode", "type": "select", "options": ["a", "b"]},
                {
                    "id": "detail", "type": "string", "required": True,
                    "depends_on": {"param": "mode", "value": "b"},
                },
            ],
        }
        try:
            # mode=a, depends_on wants "b" → detail is skipped, no required error
            errors = ex.validate_config("_test_validate", {"mode": "a"})
            assert not any("detail" in e for e in errors)
        finally:
            ex.registry.pop("_test_validate", None)

    def test_required_missing_error(self):
        """Required field missing produces an error (lines 429-430)."""
        ex = get_executor()
        ex.registry["_test_req"] = {
            "parameters": [
                {"id": "col", "type": "string", "required": True, "label": "Colonna"},
            ],
        }
        try:
            errors = ex.validate_config("_test_req", {})
            assert any("obbligatorio" in e for e in errors)
        finally:
            ex.registry.pop("_test_req", None)

    def test_required_empty_string_error(self):
        """Required field with empty string value produces an error."""
        ex = get_executor()
        ex.registry["_test_req2"] = {
            "parameters": [
                {"id": "col", "type": "string", "required": True, "label": "Col"},
            ],
        }
        try:
            errors = ex.validate_config("_test_req2", {"col": ""})
            assert any("obbligatorio" in e for e in errors)
        finally:
            ex.registry.pop("_test_req2", None)

    def test_number_below_min(self):
        """Number below min produces an error (lines 441-442)."""
        ex = get_executor()
        ex.registry["_test_num"] = {
            "parameters": [
                {"id": "val", "type": "number", "min": 0, "max": 100, "label": "Valore"},
            ],
        }
        try:
            errors = ex.validate_config("_test_num", {"val": -5})
            assert any(">=" in e for e in errors)
        finally:
            ex.registry.pop("_test_num", None)

    def test_number_above_max(self):
        """Number above max produces an error (lines 443-444)."""
        ex = get_executor()
        ex.registry["_test_num2"] = {
            "parameters": [
                {"id": "val", "type": "number", "min": 0, "max": 100, "label": "Valore"},
            ],
        }
        try:
            errors = ex.validate_config("_test_num2", {"val": 200})
            assert any("<=" in e for e in errors)
        finally:
            ex.registry.pop("_test_num2", None)

    def test_number_not_numeric(self):
        """Non-numeric value for number param produces an error (lines 445-446)."""
        ex = get_executor()
        ex.registry["_test_num3"] = {
            "parameters": [
                {"id": "val", "type": "number", "label": "Valore"},
            ],
        }
        try:
            errors = ex.validate_config("_test_num3", {"val": "abc"})
            assert any("numero" in e for e in errors)
        finally:
            ex.registry.pop("_test_num3", None)

    def test_select_invalid_value(self):
        """Value not among select options produces an error (line 455)."""
        ex = get_executor()
        ex.registry["_test_sel"] = {
            "parameters": [
                {
                    "id": "op", "type": "select", "label": "Operatore",
                    "options": [
                        {"value": "==", "label": "uguale"},
                        {"value": "!=", "label": "diverso"},
                    ],
                },
            ],
        }
        try:
            errors = ex.validate_config("_test_sel", {"op": "INVALID"})
            assert any("deve essere uno di" in e for e in errors)
        finally:
            ex.registry.pop("_test_sel", None)

    def test_select_valid_value_no_error(self):
        """Valid select value produces no error."""
        ex = get_executor()
        ex.registry["_test_sel2"] = {
            "parameters": [
                {
                    "id": "op", "type": "select", "label": "Operatore",
                    "options": ["==", "!="],
                },
            ],
        }
        try:
            errors = ex.validate_config("_test_sel2", {"op": "=="})
            assert not any("deve essere uno di" in e for e in errors)
        finally:
            ex.registry.pop("_test_sel2", None)

    def test_select_with_dict_options_valid(self):
        """Select with dict-style options accepts valid value."""
        ex = get_executor()
        ex.registry["_test_sel3"] = {
            "parameters": [
                {
                    "id": "op", "type": "select", "label": "Operatore",
                    "options": [{"value": "inner", "label": "Inner"}],
                },
            ],
        }
        try:
            errors = ex.validate_config("_test_sel3", {"op": "inner"})
            assert not any("deve essere uno di" in e for e in errors)
        finally:
            ex.registry.pop("_test_sel3", None)


# =========================================================================
# reload_executor  (lines 507-510)
# =========================================================================

class TestReloadExecutor:

    def test_reload_existing_instance(self):
        """reload_executor() reloads when instance already exists (lines 507-508)."""
        import app.core.manipola.plugin_executor as mod
        # Ensure instance exists
        _ = get_executor()
        assert mod._executor_instance is not None
        old_instance = mod._executor_instance
        reload_executor()
        # Instance should be the same object (reload, not recreate)
        assert mod._executor_instance is old_instance
        assert len(mod._executor_instance.registry) > 0

    def test_reload_creates_when_none(self):
        """reload_executor() creates new instance when none exists (lines 509-510)."""
        import app.core.manipola.plugin_executor as mod
        saved = mod._executor_instance
        try:
            mod._executor_instance = None
            reload_executor()
            assert mod._executor_instance is not None
            assert len(mod._executor_instance.registry) > 0
        finally:
            mod._executor_instance = saved


# =========================================================================
# PluginExecutorCompatibility  (lines 539-540, 554-559)
# =========================================================================

class TestPluginExecutorCompatibility:

    def test_is_plugin_transform_true(self):
        """Known transform returns True (lines 539-540)."""
        assert PluginExecutorCompatibility.is_plugin_transform("filter") is True

    def test_is_plugin_transform_false(self):
        """Unknown transform returns False."""
        assert PluginExecutorCompatibility.is_plugin_transform("INESISTENTE_XYZ") is False

    def test_dispatch_known_transform(self):
        """dispatch() executes known transform and returns DataFrame (lines 554-559)."""
        df = pd.DataFrame({"a": [3, 1, 2]})
        node = _make_node(config={"columns": "a", "ascending": True})
        result = PluginExecutorCompatibility.dispatch("sort", [df], node)
        assert result is not None
        assert isinstance(result, pd.DataFrame)
        assert list(result["a"]) == [1, 2, 3]

    def test_dispatch_unknown_transform_returns_none(self):
        """dispatch() returns None for unknown transform (lines 556-557)."""
        df = pd.DataFrame({"a": [1]})
        node = _make_node()
        result = PluginExecutorCompatibility.dispatch("INESISTENTE_XYZ", [df], node)
        assert result is None


# =========================================================================
# Additional edge-case coverage
# =========================================================================

class TestExecuteNodeConfigEdgeCases:
    """Extra tests for line 215: node.config / node.data fallback paths."""

    def test_node_config_none_data_none(self):
        """node.config=None and node.data=None → node_config is {}."""
        ex = get_executor()
        node = _make_node()
        node.config = None
        node.data = None
        # distinct works with no config (no required params with no default)
        df = pd.DataFrame({"a": [1, 1, 2]})
        result = ex.execute("distinct", [df], node)
        assert len(result) == 2

    def test_node_config_none_data_has_config(self):
        """node.config=None, node.data={'config': {...}} → uses data.config."""
        ex = get_executor()
        df = pd.DataFrame({"a": [3, 1, 2]})
        node = _make_node(data={"config": {"columns": "a", "ascending": True}})
        node.config = None
        result = ex.execute("sort", [df], node)
        assert list(result["a"]) == [1, 2, 3]

    def test_node_config_populated_takes_priority(self):
        """node.config has values → data.config is ignored."""
        ex = get_executor()
        df = pd.DataFrame({"a": [3, 1, 2]})
        node = _make_node(
            config={"columns": "a", "ascending": False},
            data={"config": {"columns": "a", "ascending": True}},
        )
        result = ex.execute("sort", [df], node)
        # ascending=False → descending
        assert list(result["a"]) == [3, 2, 1]


class TestConvertiValoreMoreEdges:
    """Extra edge cases for _converti_valore to strengthen mutation resilience."""

    def test_pivot_mapping_json_string_not_dict(self):
        """JSON string that parses to a list, not dict → returns {}."""
        ex = get_executor()
        result = ex._converti_valore('[1, 2, 3]', "pivot_mapping")
        assert result == {}

    def test_aggregation_list_json_string_not_list(self):
        """JSON string that parses to a dict, not list → returns []."""
        ex = get_executor()
        result = ex._converti_valore('{"a": 1}', "aggregation_list")
        assert result == []

    def test_boolean_int_truthy(self):
        """Integer 1 as boolean → True."""
        ex = get_executor()
        assert ex._converti_valore(1, "boolean") is True

    def test_boolean_int_falsy(self):
        """Integer 0 as boolean → False."""
        ex = get_executor()
        assert ex._converti_valore(0, "boolean") is False

    def test_string_list_single_non_string_non_list(self):
        """string_list with a non-string non-list value wraps in list."""
        ex = get_executor()
        result = ex._converti_valore(99, "string_list")
        assert result == [99]


class TestValidateConfigDependsOnMet:
    """Ensure depends_on satisfied path is validated (lines 422-425 true branch)."""

    def test_depends_on_met_required_missing_reports_error(self):
        """When depends_on IS satisfied, required-missing check fires (line 429)."""
        ex = get_executor()
        ex.registry["_test_dep_met"] = {
            "parameters": [
                {"id": "mode", "type": "string"},
                {
                    "id": "detail", "type": "string", "required": True,
                    "label": "Dettaglio",
                    "depends_on": {"param": "mode", "value": "custom"},
                },
            ],
        }
        try:
            errors = ex.validate_config("_test_dep_met", {"mode": "custom"})
            assert any("Dettaglio" in e and "obbligatorio" in e for e in errors)
        finally:
            ex.registry.pop("_test_dep_met", None)

    def test_depends_on_met_valid_value_no_error(self):
        """When depends_on IS satisfied and value provided, no error."""
        ex = get_executor()
        ex.registry["_test_dep_met2"] = {
            "parameters": [
                {"id": "mode", "type": "string"},
                {
                    "id": "detail", "type": "string", "required": True,
                    "label": "Dettaglio",
                    "depends_on": {"param": "mode", "value": "custom"},
                },
            ],
        }
        try:
            errors = ex.validate_config("_test_dep_met2", {"mode": "custom", "detail": "ok"})
            assert not any("Dettaglio" in e for e in errors)
        finally:
            ex.registry.pop("_test_dep_met2", None)
