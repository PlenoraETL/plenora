"""Modello Column del canvas — contratto strutturale condiviso (B4 roadmap).

Sede canonica del modello colonna usato dai nodi canvas: estratto da
``app.core.projects.canvas_models`` (che mantiene il re-export compatibile)
cosi' i consumer non-projects (es. ``output_contract.canvas_sync``) non
dipendono dal dominio projects per un puro contratto dati.

Boundary: leaf contract — pydantic only, nessun import app.core.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict


class Column(BaseModel):
    """Colonna di una tabella/dataset nel canvas.

    ``extra="allow"`` — simmetrico al fix di ``Node`` (commit fc86a999).
    Il frontend serializza per ogni colonna metadata extra (``selected``,
    ``fromApi``, ``path`` per output_mapping, ecc.) che il backend NON
    modella esplicitamente ma deve preservare nel round-trip
    PUT->GET->PUT del canvas. Senza ``extra="allow"`` i campi extra
    venivano droppati silenziosamente, rompendo p.es. la validation
    FE che si aspetta ``columns[i].fromApi`` per i fixed-param marker.
    """
    model_config = ConfigDict(extra="allow")
    id: Optional[str] = None
    name: str
    alias: Optional[str] = None
    visible: bool = True
    type: str = "str"

    def __getitem__(self, key: str):
        return getattr(self, key)

    def get(self, key: str, default=None):
        return getattr(self, key, default)


__all__ = ["Column"]
