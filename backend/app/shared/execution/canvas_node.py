"""
Typed accessor for canvas node dicts.

Canvas nodes arrive as plain dicts from the frontend/DB with a two-level
"""
from __future__ import annotations
from typing import Any, Optional

from app.shared.contracts.payloads import JsonObject
from app.shared.execution.node_fields import node_field


class CanvasNode:
    """Thin typed wrapper around a canvas node dict.

    Properties
    ----------
    id: node identifier (empty string if absent)
    type: node type string, e.g. ``"api_enrich"``, ``"file_source"``
    data: the nested config dict at ``node['data']`` (always a dict)
    """

    __slots__ = ("_node",)

    def __init__(self, node: JsonObject) -> None:
        if not isinstance(node, dict):
            raise TypeError(f"CanvasNode expects a dict, got {type(node).__name__}")
        self._node = node

    @property
    def id(self) -> str:
        return self._node.get("id") or ""

    @property
    def type(self) -> Optional[str]:
        return self._node.get("type")

    @property
    def data(self) -> JsonObject:
        d = self._node.get("data")
        return d if isinstance(d, dict) else {}

    def get_field(self, key: str, default: Any = None) -> Any:
        """Read a field: top-level wins if not None, else fall back to data.

        Equivalent to ``node.get(key)`` with a fallback to
        ``node['data'].get(key, default)`` when the top-level value is None.
        """
        return node_field(self._node, key, default)
