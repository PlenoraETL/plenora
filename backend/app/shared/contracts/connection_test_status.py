"""Ultimo esito del «Test connessione» nelle liste delle connessioni.

Modello puro (nessun accesso al DB): lo usano i modelli delle risposte dei
domini. La regola e la registrazione stanno in
``app/shared/connection_tests/results.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ConnectionTestStatus(BaseModel):
    """Ultimo esito del test di una connessione salvata."""

    success: bool
    message: str
    tested_at: datetime
    tested_by: Optional[str] = None
    duration_ms: int
    stale: bool


__all__ = ["ConnectionTestStatus"]
