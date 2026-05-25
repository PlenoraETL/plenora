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
Il vincolo "deep=True per cell update" valeva per pandas < 2 senza
CoW. Plenora oggi runa pandas 3.x; il vincolo storico non si applica.

Quando NON usarlo:
- Read-only path: subset/projection senza side-effect -> restituisci
  direttamente la slice (no copy, no helper). Pattern:
  return df[mask].reset_index(drop=True).

Pre-fix: df.copy() con default deep=True copiava tutti i buffer
numpy ad ogni transform -- overhead lineare in righe x colonne anche
quando il transform aggiungeva una sola colonna nuova. Su DataFrame
di milioni di righe il costo era dominante. Post-fix: deep=False
ricalca la struttura (Series/Index/dtype) ma sharing dei buffer,
~10x piu' veloce; CoW disambigua le mutazioni successive.
"""

from __future__ import annotations

from typing import TypeVar

FrameT = TypeVar("FrameT")


def copy_frame_for_update(df: FrameT) -> FrameT:
    """Return a shallow copy safe for column-level and cell-level updates.

    Relies on pandas Copy-on-Write to keep the input frame isolated
    from any subsequent mutation on the returned copy.
    """
    return df.copy(deep=False)


__all__ = ["copy_frame_for_update"]
