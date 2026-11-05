"""Tipo previsto delle colonne calcolate da una Formula (regola: decide l'esecuzione).

La previsione dava ``real`` a ogni colonna calcolata senza ``output_type``,
anche quando la formula produce testo (es. ``utente.str.slice(1, 4) + '_IS'``,
collaudo dal vivo dell'input «Utente che esegue»). Regola: il tipo e' quello
che produce l'esecuzione della STESSA formula su UNA riga di valori
rappresentativi dello schema d'ingresso (la stessa riga dell'esecuzione a vuoto
di Valida, ``one_row_frame_from_schema``). Se la prova non si puo' eseguire,
resta il ripiego di prima (``real`` o ``output_type``). ``output_type`` non e'
letto dall'esecuzione: non decide il tipo previsto. Lo stato dei tipi della Formula resta
``indeterminate``: con dati reali il tipo puo' cambiare (es. valori nulli).
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.core.dataframes.geodata import one_row_frame_from_schema
from app.core.dataframes.schema import series_to_canvas_type
from app.core.manipola.plugin_executor import get_executor
from app.shared.contracts.payloads import StringMap


def formula_column_types(input_columns: Sequence[Mapping[str, Any]], config: Mapping[str, Any]) -> StringMap:
    """{nome colonna calcolata: tipo prodotto dall'esecuzione}; {} se la prova fallisce."""
    columns = [{"name": col.get("name"), "type": col.get("type") or "str"}
               for col in input_columns if col.get("name") is not None]
    try:
        frame = one_row_frame_from_schema(columns)
        result = get_executor().execute_with_params("formula", [frame], dict(config))
    except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError, IndexError):
        return {}
    targets = [item.get("new_column") for item in (config.get("items") or []) if isinstance(item, Mapping)]
    if config.get("items") is None:
        targets = [config.get("new_column", "calculated")]
    types: StringMap = {}
    for name in targets:
        if name and name in result.columns:
            series = result[name]
            if getattr(series, "ndim", 1) == 1:
                types[name] = series_to_canvas_type(series)
    return types


__all__ = ["formula_column_types"]
