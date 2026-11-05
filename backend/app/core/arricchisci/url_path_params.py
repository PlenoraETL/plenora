"""Parametri di PERCORSO dell'URL di una connessione API (``{nome}``).

REGOLA (unica per esecuzione per riga, generatore e prova della connessione):
un valore non puo' cambiare il percorso dell'endpoint. Ogni valore si codifica
per intero (``quote(..., safe='')``: anche ``/``, ``?``, ``#``, ``%``) e un
valore che resterebbe un segmento speciale (vuoto, ``.`` o ``..``) si rifiuta.
I caratteri tra i segnaposto scritti nella connessione (per esempio la
virgola di ``{lat},{lon}``) restano come sono: e' la connessione, non il valore.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.core.arricchisci.types import JsonObject

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_UNSAFE_SEGMENTS = frozenset({"", ".", ".."})


def encode_path_param(name: str, value: Any) -> str:
    encoded = quote(str(value), safe="")
    if encoded in _UNSAFE_SEGMENTS:
        raise ValueError(
            f"Valore non valido per il parametro di percorso '{name}': "
            "vuoto, '.' o '..' cambierebbero il percorso dell'endpoint"
        )
    return encoded


def substitute_path_params(url: str, params: Mapping[str, Any]) -> tuple[str, JsonObject]:
    """``(url con i segnaposto sostituiti, parametri restanti per la query)``."""
    names = set(_PLACEHOLDER.findall(url))
    remaining = {key: value for key, value in params.items() if key not in names}
    target = _PLACEHOLDER.sub(
        lambda match: encode_path_param(match.group(1), params[match.group(1)])
        if match.group(1) in params else match.group(0),
        url,
    )
    return target, remaining


__all__ = ["encode_path_param", "substitute_path_params"]
