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
    """Schema inferito per un nodo, con esito esplicito.

    Disambigua i tre significati che collassavano su ``[]``: colonne note
    (``certain``), vuoto-ma-certo (sorgente dichiarata senza colonne), e
    **non inferibile** (``indeterminate``: upstream non risolto / output non
    dichiarato → l'engine emette un *warning*, mai un blocco).

    ``status`` è preciso e propagato a valle (un nodo indeterminate a monte rende
    indeterminate i discendenti). Vive in ``shared`` (non in ``projects``) così che
    sia il protocol di validazione sia i motori che implementano ``validate_node``
    possano riceverlo senza dipendere da ``projects/``.
    """

    columns: JsonRows
    status: str = "certain"  # "certain" | "indeterminate"
    reason: str = ""


@dataclass
class ColumnSchema:
    """Column schema inferred for a canvas node output."""

    name: str
    type: str
    id: Optional[str] = None
    source_name: Optional[str] = None
    source_node: Optional[str] = None
    is_calculated: bool = False

    def to_dict(self) -> ObjectMap:
        payload: ObjectMap = {
            "name": self.name,
            "type": self.type,
            "source_node": self.source_node,
            "is_calculated": self.is_calculated,
        }
        if self.id:
            payload["id"] = self.id
        if self.source_name:
            payload["source_name"] = self.source_name
        return payload


__all__ = ["ColumnSchema", "InferredSchema"]
