"""Typed accessor for canvas node dicts.

Canvas nodes arrive as plain dicts from the frontend/DB with a two-level
field layout: some fields sit at the top level, others are nested under
``node['data']`` (backward-compat canvas format). CanvasNode wraps one
such dict and provides typed properties plus a two-level field reader.

Mirrors the ``_nd`` / ``_nf`` helper pattern used across arricchisci and
manipola. None-only check: a top-level value of 0, False, or "" is
returned as-is; callers use ``or default`` to handle falsy-empty cases.

Domain-agnostic: no imports of Pandas, DB, FastAPI, or ArcGIS.
"""
from __future__ import annotations
from typing import Any, Optional


class CanvasNode:
    """Thin typed wrapper around a canvas node dict.

    Properties
    ----------
    id:   node identifier (empty string if absent)
    type: node type string, e.g. ``"api_enrich"``, ``"file_source"``
    data: the nested config dict at ``node['data']`` (always a dict)
    """

    __slots__ = ("_node",)

    def __init__(self, node: dict) -> None:
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
    def data(self) -> dict:
        d = self._node.get("data")
        return d if isinstance(d, dict) else {}

    def get_field(self, key: str, default: Any = None) -> Any:
        """Read a field: top-level wins if not None, else fall back to data.

        Equivalent to ``node.get(key)`` with a fallback to
        ``node['data'].get(key, default)`` when the top-level value is None.
        """
        val = self._node.get(key)
        if val is not None:
            return val
        return self.data.get(key, default)
