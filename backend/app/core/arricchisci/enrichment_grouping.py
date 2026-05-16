"""
Mixin GROUPING — raggruppamento righe per combinazione unica di

parametri (deduplicazione cache key) per `EnrichmentExecutor`.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.core.dataframes.tabular import iter_dataframe_indexed_rows
from app.core.arricchisci.types import IndexGroups


class EnrichmentGroupingMixin:
    """Mixin per `EnrichmentExecutor`: raggruppamento righe per
    combinazione unica di parametri (deduplicazione cache key).
    Pure data grouping in-memory. Zero log, zero side-effects.
    """

    def _group_by_params(self, df: Any) -> IndexGroups:
        """
        Raggruppa righe per combinazione unica di parametri (deduplicazione).

        Genera un hash MD5 per ogni combinazione di parametri (escludendo
        la API key) e raggruppa gli indici riga per hash identico.
        Questo permette di fare una sola chiamata API per N righe identiche.

        Returns:
            Dict {cache_key_md5: [lista indici riga con stessi parametri]}
        """
        groups = defaultdict(list)

        for idx, row in iter_dataframe_indexed_rows(df):
            params = self._build_params(row)
            # Rimuovi API key dalla cache key
            cache_params = {k: v for k, v in params.items()
                          if k != self.auth.get("key_name", "apiKey")}
            cache_key = self._make_cache_key(cache_params)
            groups[cache_key].append(idx)

        return dict(groups)

    def _count_unique_combinations(self, df: Any) -> int:
        """Conta combinazioni uniche di parametri."""
        return len(self._group_by_params(df))
