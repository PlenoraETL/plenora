"""Shallow copy helper for DataFrame/GeoDataFrame "modificabili".

Contratto: ritorna una NUOVA istanza che condivide i dati sottostanti
con l'input, senza copia profonda dei buffer numpy/pyarrow. Il
caller puo' aggiungere/rimuovere colonne, riassegnare colonne intere,
modificare metadata (`.attrs`, `.crs`) senza contaminare l'input.

Quando NON usarlo:
- Read-only path: subset/projection senza side-effect → restituisci
  direttamente la slice (no copy, no helper). Pattern:
  `return df[mask].reset_index(drop=True)`.
- In-place cell mutation: scrittura su singole celle via
  `.loc[row, col]` o `.iloc[...]` puo' richiedere `deep=True` per
  evitare propagazione cross-frame. Non e' il caso nei transforms
  Plenora (modifiche sempre column-level o assegnazione completa).

Pre-fix: `df.copy()` con default `deep=True` copiava tutti i buffer
numpy ad ogni transform — overhead lineare in righe×colonne anche
quando il transform aggiungeva una sola colonna nuova. Su DataFrame
di milioni di righe il costo era dominante. Post-fix: `deep=False`
ricalca la struttura (Series/Index/dtype) ma sharing dei buffer,
~10x piu' veloce; le modifiche column-level (`out[col] = ...`)
sostituiscono la Series intera quindi non c'e' aliasing reale.
"""

from __future__ import annotations

from typing import TypeVar

FrameT = TypeVar("FrameT")


def copy_frame_for_update(df: FrameT) -> FrameT:
    """Return a shallow copy safe for column-level updates."""
    return df.copy(deep=False)


__all__ = ["copy_frame_for_update"]
