"""
Mixin GROUPING — raggruppamento righe per combinazione unica di
parametri (deduplicazione cache key) per `EnrichmentExecutor`.

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service.EnrichmentExecutor`
come parte di Phase 5 Batch B3-A.8 (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; mixin pattern
ratificato in B2.1+...+B2.6 + B3-A.2/A.3/A.4/A.5/A.6/A.7).

Razionale del raggruppamento: 2 metodi cross-mixin che condividono il
dominio "raggruppamento parametri unici per deduplicazione cache":

  - `_group_by_params`               — raggruppa righe DataFrame per
                                       combinazione unica di parametri
                                       (escludendo API key) via MD5
                                       cache_key. Permette di fare una
                                       sola chiamata API per N righe
                                       identiche.
  - `_count_unique_combinations`     — `len(self._group_by_params(df))`,
                                       usato da `preview` per stimare
                                       chiamate API totali.

Cluster coeso: `_count_unique_combinations` chiama `_group_by_params`
intra-cluster. Cross-mixin: chiamati da
`EnrichmentExecutionEnginesMixin` (`_group_by_params` in tutti e 3 i
path runtime) + `EnrichmentValidationPreviewMixin`
(`_count_unique_combinations` in `preview`).

Pattern: Mixin class via MRO
============================
`EnrichmentGroupingMixin` viene ereditato da `EnrichmentExecutor`:

    class EnrichmentExecutor(
        EnrichmentTemplateParamsMixin,
        EnrichmentResponseProcessingMixin,
        EnrichmentBatchModeMixin,
        EnrichmentValidationPreviewMixin,
        EnrichmentRetryHttpMixin,
        EnrichmentExecutionEnginesMixin,
        EnrichmentGroupingMixin,
    ):
        ...

I 2 metodi restano accessibili come `EnrichmentExecutor.<method>` via
MRO. `inspect.getsource(EnrichmentExecutor.<method>)` legge il source
DA QUESTO FILE.

Coupling cross-mixin
====================
- `self._build_params(row)` → `EnrichmentTemplateParamsMixin` (B3-A.2). MRO.
- `self._make_cache_key(cache_params)` →
  `EnrichmentTemplateParamsMixin` (B3-A.2). MRO.

Coupling esterno (instance attrs)
==================================
- `self.auth` (per `auth.get("key_name", "apiKey")` API key exclusion).

Module-level deps
==================
- `pd` (pandas)
- `defaultdict` da `collections`
- `Dict, List` da `typing`

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A.8):
  - 0 `patch.object(EnrichmentExecutor, "_group_by_params" /
    "_count_unique_combinations", ...)`.
  - 0 `inspect.getsource(EnrichmentExecutor.<method>)` o
    `inspect.getsource(enrichment_service)`.
  - 0 AST scan su `enrichment_service.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr` su attributi del modulo.

Estrazione safe per costruzione. Nessuna pre-modernization richiesta.

Logger naming contract
======================
**ZERO `logger.*` calls** in entrambi i metodi (verificato awk grep
pre-extraction). Quindi il mixin NON definisce `logger`. Documentato
qui esplicitamente per parita' con `EnrichmentValidationPreviewMixin`
(B3-A.5) che ha lo stesso pattern (no log = no logger).

Behavior-preserving
===================
NESSUN cambio architetturale in B3-A.8:
  - Cache key MD5 deterministico via `self._make_cache_key(cache_params)`.
  - Esclusione API key dalla cache key per evitare miss su chiavi
    rotate (`auth.get("key_name", "apiKey")` default).
  - `defaultdict(list)` → `dict(groups)` finale per il return.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`enrichment_service.py` al momento di B3-A.8. Nessuna modifica di
logica, comment, ordine di operazione, API key exclusion, MD5 cache
key sorting (delegato a `_make_cache_key`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, List

import pandas as pd


class EnrichmentGroupingMixin:
    """Mixin per `EnrichmentExecutor`: raggruppamento righe per
    combinazione unica di parametri (deduplicazione cache key).
    Pure data grouping in-memory. Zero log, zero side-effects.
    """

    def _group_by_params(self, df: pd.DataFrame) -> Dict[str, List[int]]:
        """
        Raggruppa righe per combinazione unica di parametri (deduplicazione).

        Genera un hash MD5 per ogni combinazione di parametri (escludendo
        la API key) e raggruppa gli indici riga per hash identico.
        Questo permette di fare una sola chiamata API per N righe identiche.

        Returns:
            Dict {cache_key_md5: [lista indici riga con stessi parametri]}
        """
        groups = defaultdict(list)

        for idx, row in df.iterrows():
            params = self._build_params(row)
            # Rimuovi API key dalla cache key
            cache_params = {k: v for k, v in params.items()
                          if k != self.auth.get("key_name", "apiKey")}
            cache_key = self._make_cache_key(cache_params)
            groups[cache_key].append(idx)

        return dict(groups)

    def _count_unique_combinations(self, df: pd.DataFrame) -> int:
        """Conta combinazioni uniche di parametri."""
        return len(self._group_by_params(df))
