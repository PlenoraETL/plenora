"""Shared schema contracts for canvas execution.

Leaf module: keep this file free from project, DB, web framework, worker, and
dataframe imports so schema contracts stay portable across runtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.shared.contracts.payloads import JsonRows, ObjectMap


@dataclass(frozen=True)
class InferredSchema:
    """Schema e certezze indipendenti per nomi, tipi e ordine.

    ``names_status`` governa la validazione dei riferimenti: ``metadata``
    vale quanto ``certain`` per i nomi salvati. ``types_status`` non degrada
    gli errori sui nomi. ``order_status`` rappresenta, fra gli altri, il
    riordino saltato sui frame vuoti. ``status`` riassume tutti gli aspetti
    per i chiamanti storici; non va usato per validare un singolo aspetto.
    """

    columns: JsonRows
    status: str = "certain"  # "certain" | "indeterminate"
    reason: str = ""
    names_status: Optional[str] = None
    types_status: Optional[str] = None
    order_status: Optional[str] = None

    def __post_init__(self) -> None:
        """I chiamanti storici dichiarano entrambi gli aspetti con status."""
        if self.names_status is None:
            object.__setattr__(self, "names_status", self.status)
        if self.types_status is None:
            object.__setattr__(self, "types_status", self.status)
        if self.order_status is None:
            object.__setattr__(self, "order_status", self.status)


@dataclass
class ColumnSchema:
    """Column schema inferred for a canvas node output."""

    name: str
    type: str
    id: Optional[str] = None
    source_name: Optional[str] = None
    source_node: Optional[str] = None
    is_calculated: bool = False
    optional: bool = False

    def to_dict(self) -> ObjectMap:
        payload: ObjectMap = {
            "name": self.name,
            "type": self.type,
            "source_node": self.source_node,
            "is_calculated": self.is_calculated,
        }
        if self.optional:
            payload["optional"] = True
        if self.id:
            payload["id"] = self.id
        if self.source_name:
            payload["source_name"] = self.source_name
        return payload


__all__ = ["ColumnSchema", "InferredSchema"]
