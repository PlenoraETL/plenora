"""Normalizzazione lookup e contratto numerico condivisi con lo schema."""
from typing import Any


def lookup_value_text(value: Any) -> str:
    """Text representation used by lookup configuration values."""
    return str(value)


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
        if value is None or lookup_value_text(value).strip() == "":
            continue
        try:
            float(lookup_value_text(value).replace(",", "."))
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
