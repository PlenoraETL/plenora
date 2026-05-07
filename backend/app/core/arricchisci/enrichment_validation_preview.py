"""
Mixin VALIDATION_PREVIEW — preflight validation + preview campione per

`EnrichmentExecutor`: entry point pubblici di prova/validazione
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
                return True  # NOTE: tratta tutto come required
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

        # NOTE: se tante righe
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
