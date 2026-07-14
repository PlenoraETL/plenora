"""Shallow copy helper for DataFrame/GeoDataFrame "modificabili".

Contratto: ritorna una NUOVA istanza che condivide i dati sottostanti
con l'input, senza copia profonda dei buffer numpy/pyarrow. Il
caller puo' aggiungere/rimuovere colonne, riassegnare colonne intere,
modificare metadata (.attrs, .crs) e scrivere su singole celle via
.loc[row, col] / .iloc[...] / .at[...] senza contaminare l'input
(verificato runtime container con pandas 3.0.3, GeoDataFrame
incluso).

Su pandas >=2 con Copy-on-Write (default in pandas 3.x) la prima
mutazione cell-level su out triggera la disambiguazione automatica:
il buffer condiviso viene sostituito da una copia privata di out,
df resta intatto. Quindi `deep=False` e' safe anche per:
  - out.loc[mask, col] = value
  - out.iloc[i, j] = value
  - out.at[i, col] = value
    Con pandas Copy-on-Write attivo, non serve `deep=True` per cell update.

Quando NON usarlo:
- Read-only path: subset/projection senza side-effect -> restituisci
  direttamente la slice (no copy, no helper). Pattern:
  return df[mask].reset_index(drop=True).

`deep=False` ricalca la struttura (Series/Index/dtype) ma con sharing dei
buffer; CoW disambigua le mutazioni successive evitando copie profonde
inutili sui transform che aggiungono o riassegnano colonne.
"""

from __future__ import annotations

from typing import Any, TypeVar

import pandas as pd

FrameT = TypeVar("FrameT")


def copy_frame_for_update(df: FrameT) -> FrameT:
    """Return a shallow copy safe for column-level and cell-level updates.

    Relies on pandas Copy-on-Write to keep the input frame isolated
    from any subsequent mutation on the returned copy.
    """
    return df.copy(deep=False)


def is_string_like_series(series: Any) -> bool:
    """Return whether a series contains only string-like non-null values."""
    return pd.api.types.infer_dtype(series, skipna=True) == "string"


__all__ = ["copy_frame_for_update", "is_string_like_series"]
