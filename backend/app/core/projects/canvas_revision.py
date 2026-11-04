"""Concorrenza ottimistica sulle scritture del canvas (A3, punto 7).

Regola: la versione del canvas (``canvas_version``) è un token opaco che
cambia SOLO quando cambia il canvas salvato: impronta del contenuto
persistito (sha256 del JSON canonico letto dal DB). Valida, invalidazioni dei
progetti a valle e metadati toccano ``updated_at`` ma non il canvas, quindi
non cambiano il token. Il confronto è per UGUAGLIANZA esatta del token
restituito, dentro la stessa transazione dell'UPDATE (riga bloccata con
``FOR UPDATE``): niente confronti fra timestamp, niente precisione persa da
Date JS. PUT e ripristino restituiscono il nuovo token (e ``updated_at`` per
compatibilità); il client lo rimanda tale e quale (F3).
"""
from app.shared.contracts.payloads import JsonObject

import hashlib
import json
from datetime import datetime
from typing import Any

CANVAS_VERSION_PREFIX = "c1:"


def canvas_version(stored_canvas: Any) -> str:
    """Token del canvas persistito; dipende soltanto dal contenuto."""
    value = stored_canvas
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "replace")
    if isinstance(value, str):
        try:
            value = json.loads(value) if value else {}
        except json.JSONDecodeError:
            pass
    if value is None:
        value = {}
    text = value if isinstance(value, str) else json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return CANVAS_VERSION_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()


class CanvasRevisionConflict(ValueError):
    def __init__(self, canvas_version: str | None, updated_at: datetime | str | None = None) -> None:
        self.canvas_version = canvas_version
        self.updated_at = updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at
        super().__init__("Il canvas sul server è cambiato rispetto alla versione di base")

    def to_dict(self) -> JsonObject:
        return {"error": "validation_error", "reason": "canvas_version_conflict", "message": str(self),
                "canvas_version": self.canvas_version, "updated_at": self.updated_at}
