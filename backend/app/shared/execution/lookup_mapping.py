"""Normalizzazione lookup e contratto numerico condivisi con lo schema.

REGOLA del confronto (Blocco A, F3, seconda correzione)
-------------------------------------------------------
``op_lookup`` sceglie COME confrontare in base al tipo della colonna, con UNA
funzione dichiarata, ``lookup_match_as_text(dtype)``:

- colonna di TESTO (object, str, string, category e qualunque tipo che non
  sia numerico, booleano, data/ora o durata): esattamente il percorso storico
  (HEAD 1c654091), ``map``/``isin`` con le chiavi ORIGINALI della mappa,
  nessuna conversione. Le chiavi JSON numeriche/booleane/null ({"from": 5},
  {"from": true}, {"from": null}) si comportano come prima, collisione 1/True
  compresa; una colonna object che contiene il numero 5 NON corrisponde alla
  chiave "5" (come prima: in una colonna di testo il testo della mappa e'
  testo).
- colonna numerica, booleana, data/ora o durata (``dtype.kind`` in
  ``iufcbmM``, anche i tipi nullable Int64/Float64/boolean e le date con
  fuso): «confronta come testo» (decisione utente): chiavi e valori passano
  da ``lookup_value_text``, cosi' 5 (o 5.0) corrisponde a "5" e una data a
  "2024-01-05"; il null resta null e non corrisponde a nessuna chiave.

``POST /projects/{id}/nodes/{node}/distinct`` («Riempi con i valori presenti»)
restituisce sempre ``lookup_value_text`` (il testo che l'utente vede e mette
nella mappa), con lo stesso riconoscimento del null: ``lookup_value_text``
restituisce ``None`` se e solo se ``lookup_is_null`` e' vero.
``lookup_is_numeric`` (contratto numerico condiviso con l'inferenza) usa la
stessa forma di testo.

Forma unica (quella che un utente scriverebbe):

==========================  ===============================================
valore                      testo
==========================  ===============================================
null (None, NaN, NaT, NA,   ``None``: resta null, non corrisponde a nessuna
NaT di numpy)               chiave (mai "None"/"nan"/"NaT")
testo                       invariato
booleano                    ``True`` / ``False``
durata (timedelta di        forma di ``str(pd.Timedelta)``: ``5 days 00:00:00``
Python, numpy, pandas)      (riconosciuta PRIMA dei numeri: per numpy
                            np.timedelta64 e' un intero)
data/ora numpy              come le date qui sotto (forma di ``pd.Timestamp``)
(np.datetime64)
intero                      ``5``
decimale intero             ``5`` (5.0 -> "5", come lo scrive l'utente)
decimale                    ``2.5`` (repr di Python: 0.1 -> "0.1")
data/ora a mezzanotte       ``2024-01-05`` (solo se senza fuso)
data/ora                    ``2024-01-05 10:30:00`` (str di Python/pandas)
data                        ``2024-01-05``
altro                       ``str(value)``
==========================  ===============================================

Nessuna dipendenza da pandas (confine di ``shared``): i tipi si riconoscono
per protocollo (numbers, datetime, nome del tipo numpy) e la forma delle
durate e delle date numpy si calcola dai nanosecondi; la guardia di test la
confronta con ``str(pd.Timedelta)`` / ``str(pd.Timestamp)``.
"""
import datetime as _dt
import numbers
from typing import Any

# Oltre 2**53 un decimale non rappresenta piu' esattamente l'intero.
_EXACT_INT_LIMIT = 2 ** 53

# dtype.kind delle colonne confrontate come testo: interi con e senza segno,
# decimali, complessi, booleani, durate, date/ore (anche nullable e con fuso).
# Ogni altro dtype e' una colonna di testo (percorso storico).
_TEXT_COMPARE_KINDS = frozenset("iufcbmM")


def lookup_match_as_text(dtype: Any) -> bool:
    """True se una colonna di questo dtype si confronta come testo; False per
    le colonne di testo, che usano le chiavi originali (percorso storico)."""
    return getattr(dtype, "kind", "O") in _TEXT_COMPARE_KINDS


def _numpy_temporal(value: Any) -> str | None:
    """'datetime64' / 'timedelta64' per i valori temporali di numpy."""
    name = type(value).__name__
    return name if name in ("datetime64", "timedelta64") else None


def lookup_is_null(value: Any) -> bool:
    """Riconoscimento UNICO del null (esecuzione e distinct)."""
    if value is None:
        return True
    if type(value).__name__ in ("NAType", "NaTType"):
        return True
    if _numpy_temporal(value):
        return str(value) == "NaT"
    if isinstance(value, (str, bytes)) or isinstance(value, numbers.Integral):
        return False
    # NaN di qualunque tipo numerico (float, numpy, Decimal('NaN'), complex):
    # l'unico valore diverso da se stesso.
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def lookup_value_text(value: Any) -> str | None:
    """Forma di testo UNICA di un valore per il Lookup (vedi la tabella sopra)."""
    if lookup_is_null(value):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or type(value).__name__ == "bool_":
        return str(bool(value))
    temporal = _numpy_temporal(value)
    if temporal == "timedelta64" or isinstance(value, _dt.timedelta):
        return _timedelta_text(value)
    if temporal == "datetime64":
        return _datetime64_text(value)
    if isinstance(value, numbers.Integral):
        return str(int(value))
    if isinstance(value, numbers.Real):
        number = float(value)
        if number.is_integer() and abs(number) < _EXACT_INT_LIMIT:
            return str(int(number))
        return repr(number)
    if isinstance(value, _dt.datetime):
        midnight = (value.hour, value.minute, value.second, value.microsecond,
                    getattr(value, "nanosecond", 0)) == (0, 0, 0, 0, 0)
        if midnight and value.tzinfo is None:
            return value.date().isoformat()
        return str(value)
    if isinstance(value, _dt.date):
        return value.isoformat()
    return str(value)


_NS_PER_DAY = 86_400 * 10 ** 9


def _timedelta_text(value: Any) -> str:
    """Forma di ``str(pd.Timedelta)`` (``5 days 00:00:00``, ``-1 days +23:59:59``,
    ``0 days 00:00:01.500000``), senza pandas: dai nanosecondi totali."""
    if _numpy_temporal(value):
        if str(value.dtype).endswith(("[M]", "[Y]")):
            return str(value)  # mesi/anni: nessuna durata esatta in giorni
        try:
            ns = int(value.astype("timedelta64[ns]").astype("int64"))
        except (TypeError, ValueError, OverflowError):
            return str(value)
    elif isinstance(getattr(value, "value", None), int):
        ns = value.value  # pd.Timedelta: nanosecondi
    else:
        ns = ((value.days * 86_400 + value.seconds) * 10 ** 6 + value.microseconds) * 1000
    days, rest = divmod(ns, _NS_PER_DAY)
    seconds, frac = divmod(rest, 10 ** 9)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    text = f"{days} days{' +' if ns < 0 else ' '}{hours:02}:{minutes:02}:{seconds:02}"
    if frac:
        text += f".{frac // 1000:06}" + (f"{frac % 1000:03}" if frac % 1000 else "")
    return text


def _datetime64_text(value: Any) -> str:
    """np.datetime64 come le date (forma di ``pd.Timestamp``), senza pandas."""
    moment = value.astype("datetime64[us]").item()
    if not isinstance(moment, _dt.datetime):
        return str(value)  # fuori dall'intervallo di datetime
    nanos = int(value.astype("int64")) % 1000 if value.dtype.str.endswith("[ns]") else 0
    if nanos:
        return f"{moment}{'' if moment.microsecond else '.000000'}{nanos:03}"
    return lookup_value_text(moment)


def lookup_text_mapping(mapping: dict) -> dict:
    """Mappa con le chiavi nella forma di testo unica (solo per le colonne
    confrontate come testo); una chiave null non corrisponde a nulla e si
    scarta."""
    result = {}
    for key, target in mapping.items():
        text = lookup_value_text(key)
        if text is not None:
            result[text] = target
    return result


def normalize_lookup_mapping(mapping: dict | list | None) -> dict:
    if mapping is None:
        return {}
    if isinstance(mapping, dict):
        return mapping
    if not isinstance(mapping, list):
        raise ValueError("Lookup: mapping deve essere un dizionario o una lista.")
    result = {}
    for item in mapping:
        if isinstance(item, dict) and "old_name" in item and "new_name" in item:
            result[item["old_name"]] = item["new_name"]
        elif isinstance(item, dict) and "from" in item and "to" in item:
            result[item["from"]] = item["to"]
        else:
            raise ValueError("Lookup: ogni mapping richiede old_name/new_name oppure from/to.")
    return result


def lookup_is_numeric(mapping: dict, unmatched: str | None, default: Any) -> bool:
    # Il cast e' riservato ai config nuovi: quelli senza unmatched mantengono
    # esattamente i tipi e il comportamento storico di fillna.
    unmatched = normalize_unmatched(unmatched)
    if unmatched in (None, "keep"):
        return False
    values = list(mapping.values()) + ([default] if unmatched == "value" else [])
    for value in values:
        text = lookup_value_text(value)
        if text is None or text.strip() == "":
            continue
        try:
            float(text.replace(",", "."))
        except (ValueError, TypeError):
            return False
    return True


def normalize_unmatched(unmatched: str | None) -> str | None:
    """Empty legacy selections mean absent; all other modes are explicit."""
    if unmatched in (None, ""):
        return None
    if unmatched not in ("keep", "null", "value"):
        raise ValueError("Lookup: unmatched deve essere keep, null o value.")
    return unmatched
