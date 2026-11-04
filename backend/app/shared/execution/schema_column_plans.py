"""Tipi generati da op_aggregate; rigenerazione verificata dai contratti."""
import json
from pathlib import Path

_HERE = Path(__file__).parent
AGGREGATION_TYPES = json.loads((_HERE / "aggregation_types.json").read_text(encoding="utf-8"))
TYPE_FAMILIES = json.loads((_HERE / "type_families.json").read_text(encoding="utf-8"))


def type_family(source_type: object) -> str:
    """Famiglia di un tipo di colonna, nel vocabolario della tabella delle aggregazioni.

    Regola unica, condivisa con il frontend (copia identica di
    type_families.json): i tipi logici del contratto (integer, number,
    string, boolean, datetime), gli alias dei database e i sottotipi delle
    geometrie (geometry:point:4326) si riconducono a int/real/str/bool/date/
    datetime/geometry. Un tipo sconosciuto resta com'e' (e non e' in tabella:
    aggregation_output_type applica allora la regola di prima).
    """
    value = str(source_type or "").strip().lower()
    prefix = TYPE_FAMILIES["geometry_prefix"]
    # Come output_contract._logical_type: ogni variante "geometry..." (anche
    # "geometry(Point,4326)") e' una geometria.
    if value.startswith(prefix):
        return prefix
    return TYPE_FAMILIES["aliases"].get(value, value)


def aggregation_output_type(func: str, source_type: str) -> str:
    """Restituisce il tipo osservato o rifiuta una coppia non eseguibile.

    Se l'aggregazione non cambia la famiglia (somma di numeri, primo valore
    di una geometria) il tipo resta quello d'ingresso, con il suo vocabolario
    e il sottotipo della geometria.
    """
    family = type_family(source_type)
    table = AGGREGATION_TYPES.get(str(func).lower())
    if table is not None and family not in table:
        # Famiglia che la tabella non conosce (json, uuid, time, ...): nessun
        # falso errore; regola di prima: conteggi interi, il resto mantiene
        # il tipo d'ingresso.
        return "int" if str(func).lower() in {"count", "size", "nunique"} else source_type
    outcome = (table or {}).get(family, "error")
    if outcome == "error":
        raise TypeError(f"Aggregazione {func} non supportata per {source_type}")
    return source_type if outcome == family else outcome
