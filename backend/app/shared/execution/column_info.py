"""Contratto strutturale: informazioni di base su una colonna.

Sede canonica di ``ColumnInfo`` (estratto da
``app.core.projects.canvas_models``) cosi' gli engine orizzontali
(``dataframes``) non devono importare il dominio ``projects``.
``projects.canvas_models`` mantiene un re-export compatibile.
"""

from pydantic import BaseModel


class ColumnInfo(BaseModel):
    """Informazioni di base su una colonna (nome e tipo)."""
    name: str
    type: str = "str"
