"""
Mixin VALIDATION_PREVIEW — preflight validation + preview campione per
`EnrichmentExecutor`: entry point pubblici di prova/validazione
pre-execute().

Origine
=======
Estratto da `app.core.arricchisci.enrichment_service.EnrichmentExecutor`
come parte di Phase 5 Batch B3-A.5 (cluster split + mixin pattern,
vedi `orchestrate/PHASE_5_ENTRY_PLAN.md` §3 P5-B3-A; mixin pattern
ratificato in B2.1+...+B2.6 + B3-A.2/A.3/A.4).

Razionale del raggruppamento: 2 metodi pubblici che insieme implementano
la "fase pre-execute" di prova/validazione del DataFrame input:

  - `validate_input`     — preflight validation: colonne richieste
                           presenti (per parametri required), valori
                           nulli/vuoti su parametri required (vectorized).
                           Helper interni `_is_blocking` (closure
                           per required vs legacy) + `_required_columns`
                           (closure per template vs mapping classico).
  - `preview`            — esecuzione su campione (default
                           `options.preview_sample_size`): stima tempo
                           totale considerando deduplicazione cache +
                           parallelismo, ritorna `PreviewResult` con
                           anteprima righe + warning per dataset grandi
                           o alto error rate. Save/restore di
                           `progress_callback` locale per disabilitarlo
                           in preview mode.

Cluster coeso "preflight + preview UI". Entry point pubblici, chiamati
da:
  - `validate_input` solo da `execute()` interno (line 411).
  - `preview` esposto come pubblico (UI/router preview button), nessun
    caller interno verificato.

Pattern: Mixin class via MRO
============================
`EnrichmentValidationPreviewMixin` viene ereditato da
`EnrichmentExecutor`:

    class EnrichmentExecutor(
        EnrichmentTemplateParamsMixin,
        EnrichmentResponseProcessingMixin,
        EnrichmentBatchModeMixin,
        EnrichmentValidationPreviewMixin,
    ):
        ...

I 2 metodi restano accessibili come `EnrichmentExecutor.<method>` via
MRO. `inspect.getsource(EnrichmentExecutor.<method>)` legge il source
DA QUESTO FILE.

Coupling cross-mixin
====================
- `validate_input → self._is_template, self._template_columns` →
  `EnrichmentTemplateParamsMixin` (B3-A.2). MRO.
- `preview → self._count_unique_combinations, self._execute_internal`
  → restano in `EnrichmentExecutor` core (candidati per future batch:
  B3-A.7 ExecutionEngines per `_execute_internal`,
  `_count_unique_combinations` rimane probabilmente core).

Coupling esterno (instance attrs)
==================================
- `self.param_mapping`, `self._has_param_definitions`,
  `self._required_param_names`, `self.options`,
  `self.progress_callback` — tutti instance attrs di
  `EnrichmentExecutor.__init__`.
- `pd` (pandas), `time` (top-level).
- `EnrichmentValidationResult`, `PreviewResult` (models).

Cluster zero-transaction, zero DB I/O, zero side-effects esterni
diversi dalla mutazione temporanea di `self.progress_callback`
(save/restore local).

Coupling test contract
======================
Verificato pre-extraction (preflight B3-A.5):
  - 0 `patch.object(executor, "validate_input" / "preview", ...)` su
    `EnrichmentExecutor`.
  - 0 `inspect.getsource(EnrichmentExecutor.validate_input)` o
    `EnrichmentExecutor.preview`.
  - 0 AST scan su `enrichment_service.py` per pattern in questi metodi.
  - 0 `monkeypatch.setattr` su attributi del modulo.

Estrazione safe per costruzione. Nessuna pre-modernization richiesta.

Logger naming contract
======================
**ZERO `logger.*` calls** in entrambi i metodi (verificato awk grep
pre-extraction). Quindi il mixin NON definisce `logger`. Documentato
qui esplicitamente per non destare confusione con gli altri mixin
B3-A.x che hanno definito `logger`.

Behavior-preserving
===================
NESSUN cambio architetturale in B3-A.5:
  - Validazione: closure `_is_blocking` / `_required_columns` +
    vectorized null check via `df.index[null_mask].tolist()`.
  - Preview: save/restore `progress_callback`, sample tramite
    `df.sample(n=..., random_state=42)` o `df.copy()` se piu' piccolo.
  - Stima tempo: branching su parallelismo (`parallel_workers > 1`
    + effective_parallelism > 1) → `estimated_api_calls / rate`,
    altrimenti `(estimated_api_calls * avg_time_per_row) / 1000`.

Il body sotto e' una copia BIT-PER-BIT delle implementazioni in
`enrichment_service.py` al momento di B3-A.5. Nessuna modifica di
logica, comment, ordine di operazione, vectorized null check,
preview stima tempo branching, warning thresholds (10000 righe,
20% error rate), random_state seed.
"""

from __future__ import annotations

import time
from typing import Any, List

import pandas as pd

from app.core.arricchisci.models import (
    EnrichmentValidationResult,
    PreviewResult,
)


class EnrichmentValidationPreviewMixin:
    """Mixin per `EnrichmentExecutor`: entry point pubblici
    `validate_input` + `preview`. Pure pre-execute preparation +
    sampling. Zero log calls (verificato pre-extraction).
    """

    def validate_input(self, df: pd.DataFrame) -> EnrichmentValidationResult:
        """
        Valida il DataFrame input prima dell'esecuzione.

        Controlla:
        - Colonne richieste esistono
        - Valori nulli nei parametri

        Args:
            df: DataFrame da validare

        Returns:
            EnrichmentValidationResult con dettagli validazione
        """
        missing_columns = []
        null_param_rows = []
        warnings = []

        # Helper: True se il parametro deve essere considerato bloccante
        # (required oppure abbiamo fallback legacy senza definizioni).
        def _is_blocking(api_param: str) -> bool:
            if not self._has_param_definitions:
                return True  # legacy: tratta tutto come required
            return api_param in self._required_param_names

        # Helper: ritorna l'elenco delle colonne richieste da un mapping value.
        # - Mapping classico ("nome_col") → ["nome_col"]
        # - Template ("{X},{Y}")          → ["X", "Y"]
        def _required_columns(mapping_value: Any) -> List[str]:
            if not mapping_value:
                return []
            if self._is_template(mapping_value):
                return self._template_columns(mapping_value)
            return [mapping_value]

        # Controlla colonne richieste — solo per parametri required.
        # Le colonne mancanti per parametri opzionali sono ignorate (il param
        # verrà semplicemente omesso dalla chiamata API).
        for api_param, mapping_value in self.param_mapping.items():
            cols = _required_columns(mapping_value)
            for col in cols:
                if col not in df.columns and _is_blocking(api_param):
                    if col not in missing_columns:
                        missing_columns.append(col)

        if missing_columns:
            return EnrichmentValidationResult(
                valid=False,
                total_rows=len(df),
                valid_rows=0,
                invalid_rows=len(df),
                missing_columns=missing_columns,
                warnings=[f"Colonne mancanti: {', '.join(missing_columns)}"]
            )

        # Controlla valori nulli — solo sui parametri required (vectorized)
        # Raccogli tutte le colonne required prima
        required_cols_set = set()
        for api_param, mapping_value in self.param_mapping.items():
            if not mapping_value or not _is_blocking(api_param):
                continue
            for col in _required_columns(mapping_value):
                if col in df.columns:
                    required_cols_set.add(col)

        if required_cols_set:
            # Maschera vectorizzata: True se QUALSIASI required column è null/vuota
            null_mask = pd.Series(False, index=df.index)
            for col in required_cols_set:
                col_null = df[col].isna() | (df[col].astype(str) == "")
                null_mask = null_mask | col_null
            null_param_rows = df.index[null_mask].tolist()

        valid_rows = len(df) - len(null_param_rows)

        if null_param_rows:
            warnings.append(
                f"{len(null_param_rows)} righe hanno parametri nulli/vuoti"
            )

        return EnrichmentValidationResult(
            valid=True,
            total_rows=len(df),
            valid_rows=valid_rows,
            invalid_rows=len(null_param_rows),
            missing_columns=[],
            null_param_rows=null_param_rows,
            warnings=warnings
        )

    def preview(self, df: pd.DataFrame, sample_size: int = None) -> PreviewResult:
        """
        Esegue preview su un campione per stimare tempo e verificare funzionamento.

        Args:
            df: DataFrame completo
            sample_size: Dimensione campione (default da options)

        Returns:
            PreviewResult con stime e anteprima
        """
        sample_size = sample_size or self.options.preview_sample_size
        sample_size = min(sample_size, len(df))

        # Campiona righe
        if len(df) > sample_size:
            sample_df = df.sample(n=sample_size, random_state=42)
        else:
            sample_df = df.copy()

        # Calcola combinazioni uniche per stimare chiamate API
        unique_combinations = self._count_unique_combinations(df)

        # Esegui su campione
        start_time = time.time()

        # Disabilita progress callback per preview
        original_callback = self.progress_callback
        self.progress_callback = None

        result_df, stats, errors = self._execute_internal(sample_df)

        self.progress_callback = original_callback

        elapsed_ms = (time.time() - start_time) * 1000
        avg_time_per_row = elapsed_ms / sample_size if sample_size > 0 else 0

        # Stima tempo totale considerando deduplicazione e parallelismo
        unique_in_sample = self._count_unique_combinations(sample_df)

        # Tempo stimato = chiamate uniche / (rate_limit * min(parallel_workers, chiamate))
        estimated_api_calls = unique_combinations
        effective_parallelism = min(self.options.parallel_workers, estimated_api_calls)

        if self.options.parallel_workers > 1 and effective_parallelism > 1:
            # Con parallelismo: tempo ridotto
            rate = self.options.rate_limit if self.options.rate_limit > 0 else 10
            estimated_time = estimated_api_calls / rate
        else:
            # Sequenziale: tempo per riga * chiamate
            estimated_time = (estimated_api_calls * avg_time_per_row) / 1000

        # Warning se tante righe
        warnings = []
        if len(df) > 10000:
            warnings.append(f"Dataset grande ({len(df)} righe). Considera di usare batch.")
        if stats.errors > sample_size * 0.2:
            warnings.append(f"Alto tasso errori nel campione ({stats.errors}/{sample_size})")
        if self.options.parallel_workers > 1:
            warnings.append(f"Esecuzione parallela con {self.options.parallel_workers} worker")

        return PreviewResult(
            sample_size=sample_size,
            success_count=stats.success,
            error_count=stats.errors,
            success_rate=stats.success_rate,
            avg_time_per_row_ms=avg_time_per_row,
            estimated_total_time_seconds=estimated_time,
            estimated_api_calls=estimated_api_calls,
            unique_param_combinations=unique_combinations,
            preview_data=result_df.head(5).to_dict(orient="records"),
            errors=errors,
            warnings=warnings
        )
