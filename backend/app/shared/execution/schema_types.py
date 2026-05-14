"""Shared schema contracts for canvas execution.

Leaf module: keep this file free from project, DB, web framework, worker, and
dataframe imports so schema contracts stay portable across runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ColumnSchema:
    """Column schema inferred for a canvas node output."""

    name: str
    type: str
    source_node: Optional[str] = None
    is_calculated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "type": self.type,
            "source_node": self.source_node,
            "is_calculated": self.is_calculated,
        }


__all__ = ["ColumnSchema"]
