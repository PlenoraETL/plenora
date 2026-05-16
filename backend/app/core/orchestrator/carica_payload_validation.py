"""CARICA payload validation use-cases for orchestrator task creation."""

from __future__ import annotations

from collections.abc import Mapping


_REST_CARICA_DEST_TYPES = {"rest", "http", "https", "rest_api"}


def _nested_carica_value(carica_node_data: Mapping[str, object], key: str) -> object | None:
    if key in carica_node_data:
        return carica_node_data.get(key)
    nested = carica_node_data.get("data")
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def _rest_carica_parser_input(
    carica_node_data: Mapping[str, object],
) -> Mapping[str, object]:
    has_rest_config = isinstance(carica_node_data.get("rest_config"), dict)
    has_rest_alias = isinstance(carica_node_data.get("rest"), dict)
    if has_rest_config or has_rest_alias:
        return carica_node_data
    for key in ("destination", "target"):
        value = _nested_carica_value(carica_node_data, key)
        if isinstance(value, dict):
            return {"rest_config": value}
    return carica_node_data


def is_rest_carica_destination(carica_node_data: Mapping[str, object]) -> bool:
    dest_type = _nested_carica_value(carica_node_data, "dest_type")
    if dest_type is None:
        dest_type = _nested_carica_value(carica_node_data, "destination_type")
    return str(dest_type or "").strip().lower() in _REST_CARICA_DEST_TYPES


def validate_rest_carica_task_payload(carica_node_data: Mapping[str, object]) -> None:
    """Validate REST CARICA task configuration.

    Raises:
        ValueError: when the payload declares a REST destination but the REST
            config parser rejects its shape.
    """
    if not is_rest_carica_destination(carica_node_data):
        return

    from app.core.carica.rest_config_parser import parse_carica_rest_config

    parse_carica_rest_config(_rest_carica_parser_input(carica_node_data))
