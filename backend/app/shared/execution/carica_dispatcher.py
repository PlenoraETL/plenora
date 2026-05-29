"""Shared CARICA dispatch helpers for orchestrator and enrichment paths."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from app.shared.contracts.payloads import JsonObject
from app.shared.execution import output_contract_types_enricher


CARICA_NODE_TYPES: tuple[str, ...] = ("carica", "carica_arcgis", "datasource_node")

NODE_RESERVED_FIELDS: frozenset[str] = frozenset({
    "id",
    "type",
    "position",
    "selected",
    "dragging",
    "measured",
    "positionAbsolute",
    "width",
    "height",
})


def node_id(node: Any) -> str:
    value = _field(node, "id")
    return str(value) if value is not None else ""


def node_type(node: Any) -> str:
    value = _field(node, "type")
    return str(value) if value is not None else ""


def find_connected_carica_nodes(
    canvas: Any,
    *,
    on_unconnected: Callable[[Any], None] | None = None,
    on_missing_output: Callable[[], None] | None = None,
) -> list[Any]:
    """Return CARICA-like nodes connected directly to the result node."""

    nodes = list(_nodes(canvas))
    edges = list(_edges(canvas))

    output_node_id = next(
        (node_id(node) for node in nodes if node_type(node) == "result"),
        None,
    )
    if not output_node_id:
        if on_missing_output is not None:
            on_missing_output()
        return []

    result: list[Any] = []
    for node in nodes:
        if node_type(node) not in CARICA_NODE_TYPES:
            continue
        connected = any(
            _field(edge, "source") == output_node_id
            and _field(edge, "target") == node_id(node)
            for edge in edges
        )
        if connected:
            result.append(node)
        elif on_unconnected is not None:
            on_unconnected(node)
    return result


def build_carica_node_data(
    carica_node: Any,
    *,
    project_id: str,
    output_contract_loader: Callable[[str], object],
) -> JsonObject:
    """Build task payload data with protected identity fields and canvas types."""

    node_dict = _node_dict(carica_node)
    nested_data = node_dict.get("data") or _field(carica_node, "data") or {}
    if not isinstance(nested_data, Mapping):
        nested_data = {}

    safe_nested = {
        str(key): value
        for key, value in nested_data.items()
        if key not in NODE_RESERVED_FIELDS
    }
    carica_node_data = {**node_dict, **safe_nested}
    output_contract_types_enricher.enrich_canvas_types_from_output_contract(
        carica_node_data,
        project_id,
        output_contract_loader,
    )
    return carica_node_data


def _nodes(canvas: Any) -> Iterable[Any]:
    if isinstance(canvas, Mapping):
        raw = canvas.get("nodes", [])
    else:
        raw = getattr(canvas, "nodes", [])
    return raw or []


def _edges(canvas: Any) -> Iterable[Any]:
    if isinstance(canvas, Mapping):
        raw = canvas.get("edges", [])
    else:
        raw = getattr(canvas, "edges", [])
    return raw or []


def _node_dict(node: Any) -> JsonObject:
    if isinstance(node, Mapping):
        return dict(node)
    if hasattr(node, "model_dump"):
        dumped = node.model_dump()
        if isinstance(dumped, Mapping):
            return dict(dumped)
    return dict(vars(node))


def _field(item: Any, key: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(key)
    return getattr(item, key, None)


__all__ = [
    "CARICA_NODE_TYPES",
    "NODE_RESERVED_FIELDS",
    "build_carica_node_data",
    "find_connected_carica_nodes",
    "node_id",
    "node_type",
]
