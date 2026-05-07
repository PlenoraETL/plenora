"""
Mixin EXECUTION_ENGINES — runtime engine cuore di `EnrichmentExecutor`:

3 path di esecuzione (parallel sync via ThreadPoolExecutor, parallel
"""

from __future__ import annotations

import os
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
    """Mixin per `EnrichmentExecutor`: 3 path runtime engine.
    Cuore esecuzione alternativo scelto da `execute` dispatcher in
    base a feature flag (parallel_workers, ENABLE_ASYNC_ENRICHMENT).
    """

    def _execute_parallel(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione parallela con ThreadPoolExecutor.
        """
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []
        errors_lock = threading.Lock()

        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        total_rows = len(df)
        start_time = time.time()

        # Deduplicazione: raggruppa per parametri unici
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(
                f"Esecuzione parallela: {len(unique_groups)} combinazioni uniche "
                f"su {total_rows} righe, {self.options.parallel_workers} worker"
            )
        else:
            unique_groups = {str(idx): [idx] for idx in df.index}

        processed = [0]  # Lista per permettere modifica in closure
        processed_lock = threading.Lock()

        def process_group(cache_key: str, row_indices: List[int]) -> Tuple[str, List[int], Optional[Dict], Optional[RowError]]:
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
                )

            if self.options.enable_deduplication:
                # Prima lettura cache (senza lock — fast path
                if cache_key in self._cache:
                    return cache_key, row_indices, self._cache[cache_key], None

                # Segna come "in volo" con lock, per evitare che un secondo worker
                # faccia la stessa chiamata mentre il primo è in attesa della risposta
                with self._cache_lock:
                    if cache_key in self._cache:
                        # Altro worker ha già completato mentre attendevamo il lock
                        return cache_key, row_indices, self._cache[cache_key], None
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
                        self._cache[cache_key] = api_result
                        if len(self._cache) > self._CACHE_MAX_SIZE:
                            # Pruning FIFO: rimuove le entry più vecchie
                            keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                            for k in keys:
                                self._cache.pop(k, None)
                    else:
                        # Rimuovi placeholder: un retry futuro potrà riprovare
                        self._cache.pop(cache_key, None)

            return cache_key, row_indices, api_result, error

        # Esegui in parallelo
        with ThreadPoolExecutor(max_workers=self.options.parallel_workers) as executor:
            futures = {
                executor.submit(process_group, cache_key, row_indices): (cache_key, row_indices)
                for cache_key, row_indices in unique_groups.items()
            }

            for future in as_completed(futures):
                try:
                    cache_key, row_indices, api_result, error = future.result()

                    if error and error.status == RowStatus.SKIPPED:
                        # Righe saltate
                        with self._stats_lock:
                            stats.skipped += len(row_indices)
                    elif error:
                        # Errore - registra per tutte le righe del gruppo
                        with errors_lock:
                            for idx in row_indices:
                                errors.append(RowError(
                                    row_index=idx,
                                    status=error.status,
                                    error_message=error.error_message,
                                    error_type=error.error_type,
                                    http_status=error.http_status,
                                    retry_count=error.retry_count
                                ))

                        with self._stats_lock:
                            if error.status == RowStatus.RETRY_EXHAUSTED:
                                stats.retry_exhausted += len(row_indices)
                            else:
                                stats.errors += len(row_indices)
                            stats.api_calls_made += 1
                    else:
                        # Successo - api_result dovrebbe essere List[Dict] ma alcune
                        # API restituiscono direttamente un dict (singolo risultato
                        # Normalizziamo per evitare TypeError su `api_result[0]`
                        if isinstance(api_result, list):
                            first_row = api_result[0] if api_result else {}
                        elif isinstance(api_result, dict):
                            first_row = api_result
                        else:
                            logger.warning(
                                f"api_result tipo inatteso: {type(api_result).__name__}. "
                                f"Riga ignorata."
                            )
                            first_row = {}
                        for idx in row_indices:
                            self._apply_result(result_df, idx, first_row)

                        with self._stats_lock:
                            # Se era in cache, conta come cached
                            if cache_key in self._cache and len(row_indices) > 0:
                                # Prima riga = successo da API, resto = cached
                                stats.success += 1
                                stats.cached += len(row_indices) - 1
                                stats.api_calls_saved += len(row_indices) - 1
                            else:
                                stats.success += len(row_indices)
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

        # Finalizza statistiche
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        return result_df, stats, errors

    async def _execute_parallel_async(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """Versione async di _execute_parallel.

        Usa httpx.AsyncClient dal pool condiviso (app.shared.async_http) e un
        asyncio.Semaphore per limitare la concorrenza a ENRICHMENT_POOL_SIZE (default 50).

        Delega la chiamata HTTP effettiva al metodo sync _call_api_with_retry via
        asyncio.to_thread: questo evita di dover riscrivere tutta la logica di parsing
        risposta, output mapping, template substitution e auth. In pratica il guadagno
        viene dal parallelismo (50 task concorrenti invece di ~5 con thread pool) mentre
        la singola chiamata HTTP resta sync sotto. La Session pool di requests gia
        garantisce il connection reuse, quindi l'overhead e minimo.

        Nota: questa e la versione minima di Fase 2. Quando tutto sara stabile si potra
        passare a chiamate httpx nativamente async per eliminare il to_thread wrapper.
        """
        import asyncio

        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []

        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        total_rows = len(df)
        start_time = time.time()

        # Deduplicazione: raggruppa per parametri unici (stessa logica di _execute_parallel
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(
                f"Esecuzione async: {len(unique_groups)} combinazioni uniche "
                f"su {total_rows} righe"
            )
        else:
            unique_groups = {str(idx): [idx] for idx in df.index}

        # Semaforo per limitare la concorrenza
        # BE-CONFIG-001D: default tipato in
        # `Settings.DEFAULTS["ENRICHMENT_POOL_SIZE"]=50`. Stesso clamp
        # 5, 200] usato da `app.shared.async_http._get_pool_size`
        from app.config import settings as _settings
        pool_size = int(_settings.get("ENRICHMENT_POOL_SIZE"))
        pool_size = max(5, min(pool_size, 200))
        semaphore = asyncio.Semaphore(pool_size)
        logger.info(f"[ASYNC] Semaforo concorrenza = {pool_size}")

        processed_counter = [0]
        counter_lock = asyncio.Lock()

        async def _process_group(cache_key: str, row_indices: List) -> Optional[List[RowError]]:
            """Processa un singolo gruppo di righe con parametri identici.

            La chiamata API sync viene eseguita in un thread via asyncio.to_thread,
            cosi non blocca l'event loop.

            Ritorno: None su successo/skip, oppure List[RowError] con UN entry
            per ogni riga del gruppo (parity con il path sync _execute_parallel,
            che replica il RowError su tutti gli idx).
            """
            async with semaphore:
                first_idx = row_indices[0]
                row = df.loc[first_idx]
                # Inizializza params prima del try: in caso di eccezione in
                # _build_params, il blocco except riesce comunque a citarli
                # anche vuoti) nel RowError
                params: dict = {}

                try:
                    params = self._build_params(row)

                    # Salta se parametri nulli bloccanti
                    if self._has_null_params(params):
                        if self.options.skip_null_params:
                            stats.skipped += len(row_indices)
                            return None

                    # Cache lookup (deduplicazione tra task concorrenti
                    api_result = None
                    if self.options.enable_deduplication:
                        cached = self._cache.get(cache_key)
                        if cached and cached != "__in_flight__":
                            api_result = cached
                            stats.cached += len(row_indices)
                            self._cache_hits += len(row_indices)

                    # Se non cached: chiamata API (sync via to_thread
                    if api_result is None:
                        # Rate limiting: parity con _execute_parallel sync
                        # RateLimiter.acquire puo' bloccare via time.sleep
                        # esecuzione via to_thread evita di bloccare l'event loop
                        await asyncio.to_thread(self._rate_limiter.acquire)

                        api_result, err_obj = await asyncio.to_thread(
                            self._call_api_with_retry, params, int(first_idx)
                        )

                        if err_obj is not None:
                            stats.errors += len(row_indices)
                            # Parity con sync: replica il RowError per ogni
                            # riga del gruppo preservando http_status/retry_count
                            return [
                                RowError(
                                    row_index=int(idx),
                                    status=err_obj.status,
                                    error_message=err_obj.error_message,
                                    error_type=err_obj.error_type,
                                    http_status=err_obj.http_status,
                                    retry_count=err_obj.retry_count,
                                    input_params=err_obj.input_params or params,
                                )
                                for idx in row_indices
                            ]

                        if self.options.enable_deduplication and api_result:
                            with self._cache_lock:
                                self._cache[cache_key] = api_result
                                if len(self._cache) > self._CACHE_MAX_SIZE:
                                    keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                                    for k in keys:
                                        self._cache.pop(k, None)

                    # Applica il risultato a tutte le righe del gruppo
                    # stessa logica del path sync _execute_parallel
                    if isinstance(api_result, list):
                        first_row = api_result[0] if api_result else {}
                    elif isinstance(api_result, dict):
                        first_row = api_result
                    else:
                        first_row = {}

                    for idx in row_indices:
                        self._apply_result(result_df, idx, first_row)
                    stats.success += len(row_indices)

                    # Update progress
                    async with counter_lock:
                        processed_counter[0] += len(row_indices)
                        if self.progress_callback and processed_counter[0] % 100 == 0:
                            try:
                                self.progress_callback(processed_counter[0], total_rows)
                            except Exception as _cb_err:
                                logger.debug(
                                    f"[ASYNC] progress_callback fallita: {_cb_err}",
                                    op="execute",
                                )

                    return None

                except Exception as e:
                    logger.error(f"[ASYNC] Errore gruppo {cache_key}: {e}", exc_info=True)
                    stats.errors += len(row_indices)
                    # Parity con sync: un RowError per riga del gruppo
                    return [
                        RowError(
                            row_index=int(idx),
                            status=RowStatus.ERROR,
                            error_message=str(e),
                            error_type="async_exception",
                            input_params=params,
                        )
                        for idx in row_indices
                    ]

        # Lancia tutti i task in parallelo, gestiti dal semaforo
        tasks = [
            _process_group(ck, indices)
            for ck, indices in unique_groups.items()
        ]
        # Chunk i risultati se troppi task (evita di mantenere troppe coroutine in RAM
        CHUNK = 1000
        for chunk_start in range(0, len(tasks), CHUNK):
            chunk = tasks[chunk_start:chunk_start + CHUNK]
            results = await asyncio.gather(*chunk, return_exceptions=True)
            for r in results:
                if isinstance(r, list):
                    errors.extend(r)
                elif isinstance(r, Exception):
                    logger.error(f"[ASYNC] Exception in task: {r}")

            # Check early stop su troppi errori
            _max_err = self.options.stop_on_error_threshold
            if _max_err > 0 and stats.errors >= _max_err and not self.options.continue_on_error:
                logger.warning(f"[ASYNC] Soglia errori raggiunta ({stats.errors}/{_max_err}), interruzione")
                break

        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        logger.info(
            f"[ASYNC] Completato: {stats.success} ok, {stats.errors} err, "
            f"{stats.cached} cached, {stats.skipped} skipped in {stats.total_time_ms/1000:.1f}s"
        )
        return result_df, stats, errors

    def _execute_internal(
        self,
        df: pd.DataFrame
    ) -> Tuple[pd.DataFrame, EnrichmentStats, List[RowError]]:
        """
        Esecuzione interna sequenziale con deduplicazione e gestione errori.
        """
        result_df = df.copy()
        stats = EnrichmentStats(total_rows=len(df))
        errors: List[RowError] = []

        # Prepara colonne output
        for mapping in self.output_mapping:
            col_name = mapping.get("column", mapping.get("path"))
            if col_name not in result_df.columns:
                result_df[col_name] = None

        # Rate limiting
        delay = 1.0 / self.options.rate_limit if self.options.rate_limit > 0 else 0

        total_rows = len(df)
        start_time = time.time()

        # Deduplicazione: raggruppa per parametri unici
        if self.options.enable_deduplication:
            unique_groups = self._group_by_params(df)
            logger.info(f"Deduplicazione: {len(unique_groups)} combinazioni uniche su {total_rows} righe", op="enrich")
        else:
            # Senza deduplicazione: ogni riga è un gruppo
            unique_groups = {str(idx): [idx] for idx in df.index}

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
                for idx in row_indices:
                    self._apply_result(result_df, idx, cached_result[0] if cached_result else {})
                    stats.cached += 1
                processed += len(row_indices)
                self._update_progress(processed, total_rows, stats)
                continue

            # Chiamata API con retry
            api_result, error = self._call_api_with_retry(params, first_idx)
            stats.api_calls_made += 1

            if error:
                # Errore - registra per tutte le righe del gruppo
                for idx in row_indices:
                    errors.append(RowError(
                        row_index=idx,
                        status=error.status,
                        error_message=error.error_message,
                        error_type=error.error_type,
                        input_params=params,
                        http_status=error.http_status,
                        retry_count=error.retry_count
                    ))
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
                        self._cache[cache_key] = api_result
                        if len(self._cache) > self._CACHE_MAX_SIZE:
                            keys = list(self._cache.keys())[:self._CACHE_MAX_SIZE // 2]
                            for k in keys:
                                self._cache.pop(k, None)

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

        # Finalizza statistiche
        stats.total_time_ms = (time.time() - start_time) * 1000
        if self._response_times:
            stats.avg_response_time_ms = sum(self._response_times) / len(self._response_times)

        return result_df, stats, errors
