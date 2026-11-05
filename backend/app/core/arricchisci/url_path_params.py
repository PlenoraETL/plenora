"""Parametri di PERCORSO dell'URL di una connessione API (``{nome}``).

REGOLA (correzione finale prima del deploy, «esecuzione = verita'»)
------------------------------------------------------------------
I valori che NON vengono da un input runtime (connessione, parametri statici
della connessione, ``param_mapping`` da colonne o template) si codificano
ESATTAMENTE come in HEAD 5ea2d748, per ogni percorso:

==========================  ==========================  ===================
percorso                    segnaposto                  caratteri lasciati
==========================  ==========================  ===================
per riga (EnrichExecutor,   ``{\\w+}`` dell'URL          :data:`CONNECTION_SAFE`
worker, anteprima)
generatore                  ogni chiave dei parametri   :data:`GENERATOR_SAFE`
prova della connessione     ogni chiave dei parametri   :data:`CONNECTION_SAFE`
==========================  ==========================  ===================

Solo i valori scritti da un input runtime (``runtime_static_parameters``, che
scrive la risoluzione dei binding: ``shared.execution.runtime_bound_fields``)
seguono la regola stretta: codificati per intero (``safe=''``: anche ``/``,
``?``, ``#``, ``%``) e rifiutati se restano un segmento speciale (vuoto, ``.``,
``..``). Un valore runtime e' quello che arriva alla chiamata uguale al valore
scritto dalla risoluzione (:func:`runtime_path_keys`): se il ``param_mapping``
lo sostituisce con un valore di colonna, vale la codifica della connessione.

Un progetto senza input runtime non ha chiavi runtime: URL identico a HEAD.
Test: tests2/arricchisci/unit/test_url_path_params_parity.py.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.core.arricchisci.types import JsonObject

# HEAD 5ea2d748: per riga e prova della connessione (alcuni endpoint vogliono
# ``lat,lon``, orari ISO con ``:``, percorsi con ``/`` non codificati).
CONNECTION_SAFE = "-._~:@!$&'()*+,=;/"
# HEAD 5ea2d748: generatore, codifica stretta (senza rifiuto dei segmenti).
GENERATOR_SAFE = ""

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_UNSAFE_SEGMENTS = frozenset({"", ".", ".."})


def _same_value(left: Any, right: Any) -> bool:
    try:
        return type(left) is type(right) and bool(left == right)
    except Exception:  # noqa: BLE001 - valori non confrontabili: non runtime
        return False


def runtime_path_keys(params: Mapping[str, Any], runtime_values: Mapping[str, Any] | None) -> frozenset[str]:
    """Chiavi di ``params`` il cui valore e' quello scritto da un input runtime."""
    if not isinstance(runtime_values, Mapping):
        return frozenset()
    return frozenset(
        key for key, value in runtime_values.items()
        if key in params and _same_value(params[key], value)
    )


def encode_runtime_path_param(name: str, value: Any) -> str:
    """Valore da input runtime: codificato per intero, segmenti speciali rifiutati."""
    encoded = quote(str(value), safe="")
    if encoded in _UNSAFE_SEGMENTS:
        raise ValueError(
            f"Valore non valido per il parametro di percorso '{name}': "
            "vuoto, '.' o '..' cambierebbero il percorso dell'endpoint"
        )
    return encoded


def _encode(name: str, value: Any, safe: str, runtime_keys: frozenset[str]) -> str:
    if name in runtime_keys:
        return encode_runtime_path_param(name, value)
    return quote(str(value), safe=safe)


def substitute_row_path_params(
    url: str, params: Mapping[str, Any], *, runtime_keys: frozenset[str] = frozenset(),
) -> tuple[str, JsonObject]:
    """Per riga (HEAD 5ea2d748): segnaposto ``{\\w+}`` dell'URL, ``CONNECTION_SAFE``.

    ``(url, parametri restanti per la query)``.
    """
    call_url = url
    remaining = dict(params)
    for name in _PLACEHOLDER.findall(url):
        if name in remaining:
            encoded = _encode(name, remaining.pop(name), CONNECTION_SAFE, runtime_keys)
            call_url = call_url.replace(f"{{{name}}}", encoded)
    return call_url, remaining


def substitute_key_path_params(
    url: str, params: Mapping[str, Any], *, safe: str,
    runtime_keys: frozenset[str] = frozenset(),
) -> tuple[str, JsonObject]:
    """Generatore e prova della connessione (HEAD 5ea2d748): ogni chiave dei
    parametri presente come ``{chiave}`` nell'URL; ``safe`` del percorso."""
    target_url = url
    query_params: JsonObject = {}
    for name, value in params.items():
        placeholder = f"{{{name}}}"
        if placeholder in target_url:
            target_url = target_url.replace(placeholder, _encode(name, value, safe, runtime_keys))
        else:
            query_params[name] = value
    return target_url, query_params


__all__ = [
    "CONNECTION_SAFE",
    "GENERATOR_SAFE",
    "encode_runtime_path_param",
    "runtime_path_keys",
    "substitute_key_path_params",
    "substitute_row_path_params",
]
