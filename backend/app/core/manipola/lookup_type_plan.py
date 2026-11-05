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

Costo (Fase 5, T5+T6, correzione dopo la conferma): la previsione dipende dai
TIPI dei valori della mappa, non dal loro numero. Il piano gira su una MAPPA
RIDOTTA rappresentativa (``representative_plan``, regola sotto) e i log di
``op_lookup`` durante le prove scendono a DEBUG (``_quiet_probes``): una
mappa di 50.000 chiavi costa una lettura lineare, non 17 esecuzioni.

Ordine delle chiavi (Fase 5, T5+T6, seconda correzione; correzione
DICHIARATA): la mappa del piano e' una lista di coppie nell'ORDINE DI
INSERIMENTO, che e' l'ordine dell'esecuzione (``op_lookup``: la prima chiave
che corrisponde vince; i dtype della serie dei valori seguono quell'ordine).
Prima della correzione dopo la conferma la chiave di cache serializzava la
mappa con ``sort_keys`` e il piano girava sulle chiavi in ordine alfabetico:
su 19 configurazioni della matrice di confronto (sonda ``lkcmp``) tipo o
certezza differivano dall'esecuzione; ora coincidono con essa
(tests2/contracts/test_lookup_types_parity.py).

Prove che non si costruiscono: un ingresso di prova che ``typed_probe_frame``
non sa costruire rende il tipo INCERTO (``certain=False``), mai un'eccezione.
Una chiave che la colonna non puo' contenere (chiave-testo
"12345678901234567890" su una colonna int/Int64, oltre int64) non e' un
candidato: non corrisponde mai, come nell'esecuzione (prima: OverflowError).
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import itertools
import json
import logging
from functools import lru_cache
from typing import Any, Iterator, NamedTuple

from app.core.dataframes.schema import typed_probe_frame
from app.core.manipola.canvas_schema_runtime import build_columns_with_types
from app.core.manipola.transforms.analysis import op_lookup
from app.core.manipola.types import JsonObject
from app.shared.execution.lookup_mapping import (
    lookup_is_numeric,
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


# ── Log delle prove: DEBUG, solo nel percorso di previsione ─────────────────
# Le esecuzioni di prova di ``op_lookup`` non sono esecuzioni dell'utente: le
# loro righe (``Lookup: 1/1 valori mappati``, errori di config non valide)
# scendono a DEBUG. Il ContextVar le distingue dalle esecuzioni vere anche con
# piu' thread o richieste in parallelo (nessun livello globale cambiato).
_PREDICTING: contextvars.ContextVar[bool] = contextvars.ContextVar("lookup_type_plan_predicting", default=False)


class _ProbeLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if _PREDICTING.get() and record.levelno > logging.DEBUG:
            record.levelno = logging.DEBUG
            record.levelname = logging.getLevelName(logging.DEBUG)
        return True


_PROBE_LOGGER = logging.getLogger(op_lookup.__module__)
_PROBE_FILTER = _ProbeLogFilter()
if not any(isinstance(f, _ProbeLogFilter) for f in _PROBE_LOGGER.filters):
    _PROBE_LOGGER.addFilter(_PROBE_FILTER)


@contextlib.contextmanager
def _quiet_probes() -> Iterator[None]:
    token = _PREDICTING.set(True)
    try:
        yield
    finally:
        _PREDICTING.reset(token)


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
        # Un valore che la colonna non puo' contenere (intero oltre int64, la
        # forma di int/Int64 nei dati caricati) non e' un valore della colonna:
        # quella chiave non corrisponde mai, non e' un candidato.
        if family == "int" and not _INT64[0] <= value < _INT64[1]:
            continue
        if lookup_value_text(value) == text:
            out.append(value)
            break
    return out[:1]


def _unmatched_values(family: str, present: set[Any]) -> list[Any]:
    """Valori che non corrispondono a nessuna chiave; ``present``: testi delle chiavi."""
    if family == "bool":
        return [v for v in (True, False) if str(v) not in present][:1]
    return [v for v in _UNMATCHED.get(family, []) if lookup_value_text(v) not in present and v not in present]


class _ProbeNotBuilt(Exception):
    """L'ingresso di prova non si costruisce (es. chiave-testo oltre int64)."""


def _probe_type(column: str, column_type: str, values: list[Any], params: JsonObject) -> str | None:
    # REGOLA (Fase 5, T5+T6, seconda correzione): un ingresso di prova che non
    # si costruisce (qualunque errore di ``typed_probe_frame``, es. la chiave
    # "12345678901234567890" su una colonna int: OverflowError) rende il tipo
    # INCERTO, mai un'eccezione verso chi chiede la previsione.
    try:
        frame = typed_probe_frame(column, column_type, values)
    except Exception as exc:  # noqa: BLE001 - qualunque errore di costruzione
        raise _ProbeNotBuilt(str(exc)) from exc
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
def _plan(column_type: str, plan_json: str) -> LookupTypePlan:
    plan = json.loads(plan_json)
    column = "c"
    params = {
        "mapping": plan["mapping"],
        "default": plan["default"],
        "unmatched": plan["unmatched"],
        "output_column": "out" if plan["output_column"] else None,
    }
    family = type_family(column_type) or "str"
    try:
        keys = list(normalize_lookup_mapping(params["mapping"]).keys())
    except ValueError:
        keys = []
    groups = [_key_candidates(family, keys), plan["unmatched_probe"], [None]]
    pool = [value for group in groups for value in group]
    observed: list[str] = []
    not_built = False

    def probe(values: list[Any]) -> str | None:
        nonlocal not_built
        try:
            return _probe_type(column, column_type, values, params)
        except _ProbeNotBuilt:
            not_built = True
            return None

    with _quiet_probes():
        for size in range(len(pool) + 1):
            for combo in itertools.combinations(pool, size):
                kind = probe(list(combo))
                if kind is not None and kind not in observed:
                    observed.append(kind)
        full = probe(pool)
    predicted = full or (observed[0] if observed else column_type)
    return LookupTypePlan(predicted, len(observed) <= 1 and not not_built, tuple(observed))


# ── Mappa ridotta rappresentativa ───────────────────────────────────────────
# REGOLA: il piano gira su una mappa ridotta con lo stesso esito di tipo della
# mappa intera. Si tengono, nell'ordine della mappa:
#  (a) per ogni classe (tipo della chiave, tipo del valore) la PRIMA voce:
#      stesse famiglie di valori -> stesso dtype della serie dei valori che
#      ``op_lookup`` usa con ``map``; e la prima chiave che corrisponde alla
#      colonna resta la prima (le voci precedenti non corrispondono);
#  (b) TUTTE le voci la cui chiave corrisponde a un valore di prova (stesso
#      testo, o uguale per Python: 1 == True) -> stesse righe trovate e
#      stesso valore mappato;
#  (c) se la mappa intera esclude la conversione numerica, il primo valore
#      non numerico (``lookup_is_numeric`` da' lo stesso esito);
#  (d) i valori «non mappati» di prova si scelgono sulla mappa INTERA.
# Eccezione dichiarata: se la forma di testo fonde o scarta chiavi (due chiavi
# con lo stesso testo, una chiave null) il piano usa la mappa intera: la
# fusione puo' togliere una famiglia di valori e la riduzione non sarebbe
# esatta. Garanzia: tests2/contracts/test_lookup_types_parity.py (piano
# ridotto == piano sulla mappa intera su tutta la matrice e su mappe miste).

_INT64 = (-(2 ** 63), 2 ** 63)
_UINT64 = 2 ** 64


def _kind(value: Any) -> Any:
    """Classe di un valore per la riduzione: il tipo e, per gli interi, l'ampiezza."""
    kind = type(value)
    if kind is int:
        return (_INT64[0] <= value < _INT64[1], 0 <= value < _UINT64)
    return kind


def _plan_config(column_type: str, config: JsonObject, *, reduce: bool) -> JsonObject:
    unmatched = config.get("unmatched")
    default = config.get("default")
    family = type_family(column_type) or "str"
    base = {"default": default, "unmatched": unmatched, "output_column": bool(config.get("output_column"))}
    try:
        mapping = normalize_lookup_mapping(config.get("mapping"))
    except ValueError:
        # Mappa non valida: passa com'e' (op_lookup decide come prima).
        return {**base, "mapping": config.get("mapping"), "unmatched_probe": _unmatched_values(family, set())}
    keys = list(mapping)
    texts = [key if type(key) is str else lookup_value_text(key) for key in keys]
    present = set(texts) | {key for key in keys if isinstance(key, str)}
    unmatched_probe = _unmatched_values(family, present)
    lossy = None in present or len(set(texts)) != len(texts)
    if not reduce or lossy:
        chosen = list(range(len(keys)))
    else:
        probes = _key_candidates(family, keys) + unmatched_probe
        probe_texts = {lookup_value_text(v) for v in probes}
        probe_values = set(probes)  # 1 == True: l'uguaglianza di Python, come dict/isin
        seen: set[tuple] = set()
        chosen = []
        for index, key in enumerate(keys):
            kind = (_kind(key), _kind(mapping[key]))
            hit = texts[index] in probe_texts or key in probe_values
            if hit or kind not in seen:
                seen.add(kind)
                chosen.append(index)
        if not lookup_is_numeric(mapping, unmatched, default):
            for index, key in enumerate(keys):
                if not lookup_is_numeric({key: mapping[key]}, "null", None):
                    if index not in chosen:
                        chosen = sorted([*chosen, index])
                    break
    pairs = [{"from": keys[i], "to": mapping[keys[i]]} for i in chosen]
    return {**base, "mapping": pairs, "unmatched_probe": unmatched_probe}


def representative_plan(column_type: str, config: JsonObject) -> JsonObject:
    """Configurazione del piano sulla mappa ridotta (vedi la REGOLA sopra)."""
    return _plan_config(column_type, config, reduce=True)


def _run(column_type: str, plan: JsonObject) -> LookupTypePlan:
    return _plan(column_type, json.dumps(plan, sort_keys=True, default=str))


def full_mapping_type_plan(column_type: str | None, config: JsonObject) -> LookupTypePlan:
    """Piano sulla mappa INTERA: solo riferimento dei test di parita'."""
    column_type = str(column_type or "str")
    return _run(column_type, _plan_config(column_type, config, reduce=False))


def lookup_type_plan(column_type: str | None, config: JsonObject) -> LookupTypePlan:
    """Tipo della colonna di uscita del Lookup sul tipo ``column_type`` in ingresso."""
    column_type = str(column_type or "str")
    return _run(column_type, representative_plan(column_type, config))


__all__ = ["LookupTypePlan", "full_mapping_type_plan", "lookup_type_plan", "representative_plan"]
