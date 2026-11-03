"""Declared registry types and their conversion/validation policy."""
from typing import Any, Mapping
LIST_PARAM_TYPES = frozenset({"aggregation_list", "filter_conditions", "formula_items", "select_columns_list", "rename_list", "condition_list", "masking_list"})
PASSTHROUGH_PARAM_TYPES = frozenset({"column", "geometry_column", "expression", "select", "string"})
KNOWN_PARAM_TYPES = LIST_PARAM_TYPES | PASSTHROUGH_PARAM_TYPES | {"number", "boolean", "columns", "string_list", "pivot_mapping", "value_mapping"}


def require_known_param_type(param_type: str | None) -> None:
    if param_type is not None and param_type not in KNOWN_PARAM_TYPES:
        raise ValueError(f"Unknown registry parameter type: {param_type}")


PARAM_ALIASES = {"left_keys": "left_on", "right_keys": "right_on"}


def parameter_value(config: Mapping[str, Any], param_id: str, default: Any = None) -> Any:
    """Canonical keys win; absent/null keys accept persisted legacy aliases."""
    value = config.get(param_id)
    if value is None:
        return config.get(PARAM_ALIASES.get(param_id), default)
    return value
