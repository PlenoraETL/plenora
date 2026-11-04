"""Identità delle selezioni Output, inclusi i formati storici del canvas."""

from app.shared.contracts.payloads import JsonRows
from typing import Mapping

from app.shared.execution.config_column_refs import resolve_config_column_refs


def resolve_output_column_refs(columns: JsonRows, id_to_name: Mapping[str, str]) -> JsonRows:
    """L'identità esplicita prevale sul nome salvato prima della rinomina."""
    normalized = [dict(col, name_column_id=col.get("name_column_id") or col.get("id") or col.get("column_id"))
                  if isinstance(col, dict) else col for col in columns]
    return resolve_config_column_refs({"columns": normalized}, id_to_name)["columns"]


def unique_column_names(columns: list[dict]) -> dict[str, str]:
    """Esclude le identit? ambigue: il chiamante ripiega sul nome salvato."""
    result = {}
    ambiguous = set()
    for col in columns:
        if not isinstance(col, dict) or not col.get("name"):
            continue
        identity = col.get("id") or col.get("column_id")
        if not identity:
            continue
        identity = str(identity)
        if identity in result:
            ambiguous.add(identity)
        result[identity] = col["name"]
    return {identity: name for identity, name in result.items() if identity not in ambiguous}
