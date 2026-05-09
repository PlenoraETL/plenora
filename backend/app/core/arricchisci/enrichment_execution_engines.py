"""
Mixin EXECUTION_ENGINES — runtime engine cuore di `EnrichmentExecutor`.

Path di esecuzione sync: parallelo via ThreadPoolExecutor e sequenziale.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import pandas as pd

from app.core.arricchisci.models import EnrichmentStats, RowError, RowStatus
from app.utils.logger import get_logger

# Stesso logger name di `enrichment_service.py` (`__name__` valuta a
# `"app.core.arricchisci.enrichment_service"`). Singleton stdlib per
# nome → record indistinguibili dai consumer che attaccano handler /
# filtrano per nome canonico
logger = get_logger("app.core.arricchisci.enrichment_service")


class EnrichmentExecutionEnginesMixin:
    """Mixin per `EnrichmentExecutor`: runtime engine sync.

    `execute` sceglie tra batch nativo, ThreadPoolExecutor e path
    sequenziale in base alla configurazione del batch e a parallel_workers.
    """

    def _serial_engine_required_reason(self) -> Optional[str]:
        """Ritorna il motivo per cui serve il motore sequenziale.

        Il path ThreadPoolExecutor e' un motore throughput-only per workload
        indipendenti dove ogni gruppo produce una riga logica e non sono
        richieste semantiche di stop anticipato. Il path sequenziale e' il
        motore canonico per preview e per le opzioni che dipendono dall'ordine
        di elaborazione o dalla gestione per-riga degli errori.
        """
        if not self.options.skip_null_params:
            return "skip_null_params=False"
        if not self.options.continue_on_error:
            return "continue_on_error=False"
        if self.options.stop_on_error_threshold > 0:
            return "stop_on_error_threshold"
        if self.auth.get("type") in ("oauth2_password", "oauth2_client_credentials"):
            return "oauth2_header_refresh"
        return None

    def _should_use_parallel_engine(self) -> bool:
        """True quando il dispatcher pubblico puo' usare ThreadPoolExecutor."""
        return (
            self.options.parallel_workers > 1
            and self._serial_engine_required_reason() is None
        )

    def _start_execution_state(
        self,
        df: pd.DataFrame,
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError], int, float]:
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []

        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        return result_df, stats, errors, len(df), time.time()

    def _build_execution_groups(
        self,
        df: pd.DataFrame,
    ) -> Dict[str, List[int]]:
        if self.options.enable_deduplication:
            return self._group_by_params(df)
        return {str(idx): [idx] for idx in df.index}

    def _prune_cache_if_needed(self) -> None:
        if len(self._cache) <= self._CACHE_MAX_SIZE:
            return

        keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
        for key in keys:
            self._cache.pop(key, None)

    def _store_cache_result(self, cache_key: str, api_result) -> None:
        self._cache[cache_key] = api_result
        self._prune_cache_if_needed()

    def _first_result_row(self, api_result) -> Dict:
        if isinstance(api_result, list):
            return api_result[0] if api_result else {}
        if isinstance(api_result, dict):
            return api_result

        logger.warning(
            f"api_result tipo inatteso: {type(api_result).__name__}. "
            f"Riga ignorata."
        )
        return {}

    def _append_group_errors(
        self,
        errors: List[RowError],
        row_indices: List[int],
        error: RowError,
        *,
        input_params: Optional[Dict] = None,
    ) -> None:
        for idx in row_indices:
            errors.append(RowError(
                row_index=idx,
                status=error.status,
                error_message=error.error_message,
                error_type=error.error_type,
                input_params=input_params,
                http_status=error.http_status,
                retry_count=error.retry_count,
            ))

    def _finish_execution_stats(
        self,
        stats: EnrichmentStats,
        start_time: float,
    ) -> None:
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = (
                sum(self._response_times) / len(self._response_times)
            )

    def _execute_parallel(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione parallela con ThreadPoolExecutor.

        Se il caller ha richiesto semantiche serial-only, delega al motore
        sequenziale: questo mantiene il comportamento pubblico di `execute`
        anche quando il dispatcher storico entra qui con parallel_workers > 1.
        """
        serial_reason = self._serial_engine_required_reason()
        if serial_reason is not None:
            logger.info(
                f"Esecuzione sequenziale richiesta ({serial_reason}); "
                "ThreadPoolExecutor non usato."
            )
            return self._execute_internal(df)

        result_df, stats, errors, total_rows, start_time = (
            self._start_execution_state(df)
        )
        errors_lock = threading.Lock()

        # Deduplicazione: raggruppa per parametri unici
        unique_groups = self._build_execution_groups(df)
        if self.options.enable_deduplication:
            logger.info(
                f"Esecuzione parallela: {len(unique_groups)} combinazioni uniche "
                f"su {total_rows} righe, {self.options.parallel_workers} worker"
            )

        processed = [0]  # Lista per permettere modifica in closure
        processed_lock = threading.Lock()

        def process_group(
            cache_key: str,
            row_indices: List[int],
        ) -> Tuple[str, List[int], Optional[Dict], Optional[RowError], bool]:
            """Worker function per processare un gruppo.

            Double-checked locking per evitare cache stampede:
            se due worker ricevono lo stesso cache_key contemporaneamente,
            solo uno fa la chiamata API — l'altro aspetta e usa il risultato in cache.
            """
            first_idx = row_indices[0]
            row = df.loc[first_idx]

            # Costruisci parametri
            params = self._build_params(row)

            # Salta se parametri nulli
            if self.options.skip_null_params and self._has_null_params(params):
                return cache_key, row_indices, None, RowError(
                    row_index=first_idx,
                    status=RowStatus.SKIPPED,
                    error_message="Parametri nulli",
                    error_type="null_params"
                ), False

            if self.options.enable_deduplication:
                # Prima lettura cache (senza lock — fast path
                if cache_key in self._cache:
                    return cache_key, row_indices, self._cache[cache_key], None, False

                # Segna come "in volo" con lock, per evitare che un secondo worker
                # faccia la stessa chiamata mentre il primo è in attesa della risposta
                with self._cache_lock:
                    if cache_key in self._cache:
                        # Altro worker ha già completato mentre attendevamo il lock
                        return cache_key, row_indices, self._cache[cache_key], None, False
                    # Placeholder: indica che questo worker sta elaborando il gruppo
                    self._cache[cache_key] = "__in_flight__"

            # Rate limiting
            self._rate_limiter.acquire()

            # Chiamata API
            api_result, error = self._call_api_with_retry(params, first_idx)

            # Aggiorna cache con risultato reale (o rimuovi placeholder in caso di errore
            if self.options.enable_deduplication:
                with self._cache_lock:
                    if api_result:
                        self._store_cache_result(cache_key, api_result)
                    else:
                        # Rimuovi placeholder: un retry futuro potrà riprovare
                        self._cache.pop(cache_key, None)

            return cache_key, row_indices, api_result, error, True

        # Esegui in parallelo
        with ThreadPoolExecutor(max_workers=self.options.parallel_workers) as executor:
            futures = {
                executor.submit(process_group, cache_key, row_indices): (cache_key, row_indices)
                for cache_key, row_indices in unique_groups.items()
            }

            for future in as_completed(futures):
                try:
                    cache_key, row_indices, api_result, error, api_called = future.result()

                    if error and error.status == RowStatus.SKIPPED:
                        # Righe saltate
                        with self._stats_lock:
                            stats.skipped += len(row_indices)
                    elif error:
                        # Errore - registra per tutte le righe del gruppo
                        with errors_lock:
                            self._append_group_errors(errors, row_indices, error)

                        with self._stats_lock:
                            if error.status == RowStatus.RETRY_EXHAUSTED:
                                stats.retry_exhausted += len(row_indices)
                            else:
                                stats.errors += len(row_indices)
                            if api_called:
                                stats.api_calls_made += 1
                    else:
                        first_row = self._first_result_row(api_result)
                        for idx in row_indices:
                            self._apply_result(result_df, idx, first_row)

                        with self._stats_lock:
                            if not api_called:
                                stats.cached += len(row_indices)
                            # Se era in cache dopo una chiamata appena fatta,
                            # conta la prima riga come success e le duplicate
                            # come cache/dedup intra-run.
                            elif cache_key in self._cache and len(row_indices) > 0:
                                # Prima riga = successo da API, resto = cached
                                stats.success += 1
                                stats.cached += len(row_indices) - 1
                                stats.api_calls_saved += len(row_indices) - 1
                            else:
                                stats.success += len(row_indices)
                            if api_called:
                                stats.api_calls_made += 1

                    # Update progress
                    with processed_lock:
                        processed[0] += len(row_indices)
                        self._update_progress(processed[0], total_rows, stats)

                except Exception as e:
                    logger.error(f"[CRASH] Errore in worker: {e}", exc_info=True)
                    cache_key, row_indices = futures[future]
                    with errors_lock:
                        for idx in row_indices:
                            errors.append(RowError(
                                row_index=idx,
                                status=RowStatus.ERROR,
                                error_message=str(e),
                                error_type="worker_error"
                            ))
                    with self._stats_lock:
                        stats.errors += len(row_indices)

        self._finish_execution_stats(stats, start_time)

        return result_df, stats, errors

    def _execute_internal(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione interna sequenziale con deduplicazione e gestione errori.
        """
        result_df, stats, errors, total_rows, start_time = (
            self._start_execution_state(df)
        )

        # Rate limiting
        delay = 1.0 / self.options.rate_limit if self.options.rate_limit > 0 else 0

        # Deduplicazione: raggruppa per parametri unici
        unique_groups = self._build_execution_groups(df)
        if self.options.enable_deduplication:
            logger.info(f"Deduplicazione: {len(unique_groups)} combinazioni uniche su {total_rows} righe", op="enrich")

        processed = 0
        _extra_rows: List[Dict] = []  # righe espanse da FeatureCollection o array
        # Token OAuth2 gestito via cache con expires_at (api_utils.get_cached_oauth2_token
        # Il refresh avviene automaticamente quando serve — non piu counter arbitrario
        _is_oauth2 = self.auth.get("type") in ("oauth2_password", "oauth2_client_credentials")

        for cache_key, row_indices in unique_groups.items():
            # Per OAuth2: refresh headers (la cache evita HTTP se il token e ancora valido
            if _is_oauth2:
                self._headers = self._prepare_headers()

            # Prendi la prima riga del gruppo per i parametri
            first_idx = row_indices[0]
            row = df.loc[first_idx]

            # Costruisci parametri
            params = self._build_params(row)

            # Salta se parametri nulli
            if self._has_null_params(params):
                if self.options.skip_null_params:
                    for idx in row_indices:
                        stats.skipped += 1
                    processed += len(row_indices)
                    self._update_progress(processed, total_rows, stats)
                    continue
                else:
                    # skip_null_params=False -> null è un errore
                    for idx in row_indices:
                        errors.append(RowError(
                            row_index=idx,
                            status=RowStatus.ERROR,
                            error_message="Parametro obbligatorio null",
                            error_type="null_param",
                            input_params=params
                        ))
                        stats.errors += 1
                    if not self.options.continue_on_error:
                        logger.warning(f"Parametro null su riga {first_idx}, continue_on_error=False -> interruzione")
                        processed += len(row_indices)
                        break
                    processed += len(row_indices)
                    self._update_progress(processed, total_rows, stats)
                    continue

            # Controlla cache
            if self.options.enable_deduplication and cache_key in self._cache:
                # Cache hit - cached_result è List[Dict
                cached_result = self._cache[cache_key]
                first_row = self._first_result_row(cached_result)
                for idx in row_indices:
                    self._apply_result(result_df, idx, first_row)
                    stats.cached += 1
                processed += len(row_indices)
                self._update_progress(processed, total_rows, stats)
                continue

            # Chiamata API con retry
            api_result, error = self._call_api_with_retry(params, first_idx)
            stats.api_calls_made += 1

            if error:
                # Errore - registra per tutte le righe del gruppo
                self._append_group_errors(
                    errors,
                    row_indices,
                    error,
                    input_params=params,
                )
                for idx in row_indices:
                    if error.status == RowStatus.RETRY_EXHAUSTED:
                        stats.retry_exhausted += 1
                        stats.errors += 1  # B-BE-56: conta anche nel threshold
                    else:
                        stats.errors += 1
                # Stop immediato se continue_on_error=False
                if not self.options.continue_on_error:
                    logger.warning(f"Errore su riga {first_idx}, continue_on_error=False -> interruzione")
                    processed += len(row_indices)
                    break
            else:
                # Successo - api_result è ora una List[Dict
                # Se la lista ha >1 elementi (es. FeatureCollection), esplodi le righe
                if self.options.enable_deduplication:
                    with self._cache_lock:
                        self._store_cache_result(cache_key, api_result)

                for idx in row_indices:
                    if len(api_result) == 1:
                        # Caso normale: 1 dict -> scrivi nella riga esistente
                        self._apply_result(result_df, idx, api_result[0])
                        stats.success += 1
                    elif len(api_result) > 1:
                        # FeatureCollection o array: prima feature nella riga esistente
                        # le restanti come righe extra (con i parametri di input replicati
                        self._apply_result(result_df, idx, api_result[0])
                        input_row_data = result_df.loc[idx].to_dict()
                        for extra_dict in api_result[1:]:
                            new_row = {**input_row_data, **extra_dict}
                            _extra_rows.append(new_row)
                        stats.success += 1

                # Chiamate risparmiate = righe gruppo - 1
                stats.api_calls_saved += len(row_indices) - 1

            processed += len(row_indices)
            self._update_progress(processed, total_rows, stats)

            # Rate limiting
            if delay > 0:
                time.sleep(delay)

            # Check error threshold
            if (self.options.stop_on_error_threshold > 0 and
                stats.errors >= self.options.stop_on_error_threshold):
                logger.warning(f"Raggiunto threshold errori ({stats.errors}), interruzione")
                break

        # Concatena righe espanse (FeatureCollection explosion
        if _extra_rows:
            extra_df = pd.DataFrame(_extra_rows)
            result_df = pd.concat([result_df, extra_df], ignore_index=True)
            logger.info(f"Esplosione FeatureCollection: aggiunte {len(_extra_rows)} righe extra")

        self._finish_execution_stats(stats, start_time)

        return result_df, stats, errors
