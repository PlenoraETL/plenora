"""Tipo della colonna prodotta dal Lookup = tipo prodotto dall'esecuzione.

Fase 5, T6 (residuo BA1 del Blocco A): la previsione non copia piu' le regole
di ``op_lookup`` (dtype di ``map``/``where``/``fillna``, confronto come testo
sulle colonne non testo, conversione numerica dell'uscita): le ESEGUE.
``op_lookup`` gira su piccoli ingressi rappresentativi della colonna (stesso
dtype dei dati caricati) e il tipo e' quello che ne esce, letto con la stessa
regola dei tipi dell'esecuzione (``build_columns_with_types``).

Ingressi rappresentativi: tutte le combinazioni di una riga che corrisponde a
una chiave della mappa, righe che non corrispondono (per i decimali: intera e
non intera) e una riga null, compreso l'ingresso vuoto.

- Tutte le combinazioni danno lo stesso tipo -> tipo CERTO (e' quello).
- Tipi diversi -> il tipo dipende dai valori (es. conversione numerica: intero
  se ogni riga corrisponde, decimale con un null): ``certain=False`` e le
  alternative possibili; la previsione e' il tipo dell'ingresso con tutti i
  casi insieme.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
from functools import lru_cache
from typing import Any, NamedTuple

from app.core.dataframes.schema import typed_probe_frame
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.manipola.transforms.analysis import op_lookup
from app.core.manipola.types import JsonObject
from app.shared.execution.lookup_mapping import (
    lookup_value_text,
    normalize_lookup_mapping,
)
from app.shared.execution.schema_column_plans import type_family


class LookupTypePlan(NamedTuple):
    type: str
    certain: bool
    alternatives: tuple[str, ...]


_UNMATCHED = {
    "int": [-987654321],
    "real": [0.25, 7.0],
    "str": ["⁣plenora-non-mappato"],
    "date": ["1999-12-31"],
    "datetime": ["1999-12-31 10:30:00"],
}


def _key_candidates(family: str, keys: list[Any]) -> list[Any]:
    """Valori della colonna che corrispondono a una chiave (stessa forma di testo)."""
    out: list[Any] = []
    for key in keys:
        if family == "str":
            if isinstance(key, str):
                out.append(key)
            continue
        text = lookup_value_text(key)
        if text is None:
            continue
        try:
            if family == "int":
                value: Any = int(text)
            elif family == "real":
                value = float(text)
            elif family == "bool":
                if text not in ("True", "False"):
                    continue
                value = text == "True"
            elif family in ("date", "datetime"):
                value = _dt.datetime.fromisoformat(text)
            else:
                continue
        except (ValueError, TypeError):
            continue
        if lookup_value_text(value) == text:
            out.append(value)
    return out[:1]


def _unmatched_values(family: str, keys: list[Any]) -> list[Any]:
    texts = {lookup_value_text(k) for k in keys} | {k for k in keys if isinstance(k, str)}
    if family == "bool":
        return [v for v in (True, False) if str(v) not in texts][:1]
    return [v for v in _UNMATCHED.get(family, []) if lookup_value_text(v) not in texts and v not in texts]


def _probe_type(column: str, column_type: str, values: list[Any], params: JsonObject) -> str | None:
    frame = typed_probe_frame(column, column_type, values)
    try:
        result = op_lookup(frame, column=column, **params)
    except (ValueError, TypeError, KeyError):
        return None
    target = params.get("output_column") or column
    for col in build_columns_with_types(result, {}):
        if col["name"] == target:
            return col["type"]
    return None


@lru_cache(maxsize=512)
def _plan(column_type: str, config_json: str) -> LookupTypePlan:
    config = json.loads(config_json)
    column = "c"
    params = {
        "mapping": config.get("mapping"),
        "default": config.get("default"),
        "unmatched": config.get("unmatched"),
        "output_column": "out" if config.get("output_column") else None,
    }
    family = type_family(column_type) or "str"
    try:
        keys = list(normalize_lookup_mapping(params["mapping"]).keys())
    except ValueError:
        keys = []
    groups = [_key_candidates(family, keys), _unmatched_values(family, keys), [None]]
    pool = [value for group in groups for value in group]
    observed: list[str] = []
    for size in range(len(pool) + 1):
        for combo in itertools.combinations(pool, size):
            kind = _probe_type(column, column_type, list(combo), params)
            if kind is not None and kind not in observed:
                observed.append(kind)
    full = _probe_type(column, column_type, pool, params)
    predicted = full or (observed[0] if observed else column_type)
    return LookupTypePlan(predicted, len(observed) <= 1, tuple(observed))


def lookup_type_plan(column_type: str | None, config: JsonObject) -> LookupTypePlan:
    """Tipo della colonna di uscita del Lookup sul tipo ``column_type`` in ingresso."""
    key = json.dumps({k: config.get(k) for k in ("mapping", "default", "unmatched", "output_column")},
                     sort_keys=True, default=str)
    return _plan(str(column_type or "str"), key)


__all__ = ["LookupTypePlan", "lookup_type_plan"]
