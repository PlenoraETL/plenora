"""Accesso unico al mapping API; preserva le due fasi del runtime storico."""
from typing import Literal

from app.shared.contracts.payloads import JsonObject


def mapping_destination(
    mapping: JsonObject, *, phase: Literal["response", "source", "columns", "explicit"] = "response",
    default: str | None = None,
) -> str | None:
    if phase == "explicit":
        return mapping.get("column", default)
    if phase == "source":
        return mapping.get("path", default)
    if phase == "columns":
        return mapping.get("column", mapping.get("path", default))
    return mapping.get("column") or mapping.get("path", default)
