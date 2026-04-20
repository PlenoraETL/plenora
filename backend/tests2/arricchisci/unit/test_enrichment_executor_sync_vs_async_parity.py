"""
Parity test tra _execute_parallel (sync) e _execute_parallel_async (async).

Contesto
========
Il flag ENABLE_ASYNC_ENRICHMENT=true e' attivo in produzione
(/home/ubuntu/plenora-etl/backend/.env). I due path vivono insieme.
Questi test fissano il contratto osservabile atteso per entrambi.

Tassonomia:
- T1, T2, T3, T5 = parity stabile (gia' passanti pre-fix).
- T4, T6         = parity di hardening (rossi pre-fix del path async, verdi dopo).
- T7, T8         = divergenze accettate: documentano il comportamento attuale
                   di ciascun path senza forzarne l'allineamento.

Nota su T3: lo "stampede cache" teorico non e' osservabile perche'
_group_by_params mappa ogni cache_key a un singolo task. Il test resta a
presidio: se qualcuno in futuro cambia la costruzione di unique_groups,
la regressione emergerebbe qui.

Vincoli del test:
- Nessuna rete: _call_api_with_retry monkeypatchato a livello di istanza.
- Nessun DB: EnrichmentExecutor non tocca il catalogo PG.
- Nessun import di app.db.*: il conftest di unit/ risolve eventuali
  dipendenze tramite ApiConnectionService, non toccato qui.
"""
import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import pytest

from app.core.arricchisci.enrichment_service import EnrichmentExecutor
from app.core.arricchisci.models import (
    EnrichmentOptions,
    RowError,
    RowStatus,
)


pytestmark = [pytest.mark.unit, pytest.mark.arricchisci]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_executor(
    *,
    rate_limit: float = 0.0,
    parallel_workers: int = 5,
    max_retries: int = 0,
    enable_deduplication: bool = True,
    skip_null_params: bool = True,
    stop_on_error_threshold: int = 0,
    continue_on_error: bool = True,
    progress_callback=None,
) -> EnrichmentExecutor:
    """Costruisce un executor con config minimale adeguata per i test parity."""
    api_config = {
        "name": "TestAPI",
        "endpoint": {"url": "https://api.test/lookup", "method": "GET"},
        "auth": {},
        "output_mapping": [{"path": "result", "column": "result"}],
    }
    options = EnrichmentOptions(
        validate_input=False,
        enable_deduplication=enable_deduplication,
        rate_limit=rate_limit,
        parallel_workers=parallel_workers,
        max_retries=max_retries,
        skip_null_params=skip_null_params,
        stop_on_error_threshold=stop_on_error_threshold,
        continue_on_error=continue_on_error,
    )
    return EnrichmentExecutor(
        api_config=api_config,
        param_mapping={"q": "q"},
        options=options,
        progress_callback=progress_callback,
    )


def _run_sync(executor, df):
    return executor._execute_parallel(df)


def _run_async(executor, df):
    return asyncio.run(executor._execute_parallel_async(df))


def _install_fake_call(executor, fn):
    """Sostituisce _call_api_with_retry a livello di istanza."""
    executor._call_api_with_retry = fn


# ---------------------------------------------------------------------------
# T1 — Happy path, no dedup (parity stabile)
# ---------------------------------------------------------------------------
def test_t1_happy_path_parity_no_dedup(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": f"out-{params.get('q')}"}, None

    sync_exec = _make_executor(enable_deduplication=False)
    _install_fake_call(sync_exec, fake_call)
    sync_df, sync_stats, sync_errors = _run_sync(sync_exec, df)

    async_exec = _make_executor(enable_deduplication=False)
    _install_fake_call(async_exec, fake_call)
    async_df, async_stats, async_errors = _run_async(async_exec, df)

    assert list(sync_df["result"]) == list(async_df["result"])
    assert sync_stats.success == async_stats.success == 10
    assert sync_stats.errors == async_stats.errors == 0
    assert len(sync_errors) == len(async_errors) == 0


# ---------------------------------------------------------------------------
# T2 — Dedup con 3 cache_key distinti (parity stabile)
# ---------------------------------------------------------------------------
def test_t2_dedup_three_cache_keys_parity(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    values = ["a", "b", "c", "a", "b", "c", "a", "b", "c", "a"]
    df = pd.DataFrame({"q": values})

    def make_counting_fake():
        calls = []

        def fake_call(params, row_index):
            calls.append(params.get("q"))
            return {"result": f"out-{params.get('q')}"}, None

        return fake_call, calls

    sync_fake, sync_calls = make_counting_fake()
    sync_exec = _make_executor()
    _install_fake_call(sync_exec, sync_fake)
    sync_df, sync_stats, _ = _run_sync(sync_exec, df)

    async_fake, async_calls = make_counting_fake()
    async_exec = _make_executor()
    _install_fake_call(async_exec, async_fake)
    async_df, async_stats, _ = _run_async(async_exec, df)

    # Stesso numero di chiamate effettive: una per cache_key unico.
    assert len(sync_calls) == 3
    assert len(async_calls) == 3
    # Output uguali per ciascun indice.
    assert list(sync_df["result"]) == list(async_df["result"])


# ---------------------------------------------------------------------------
# T3 — Cache stampede (HARDENING: rosso pre-fix, verde post-fix)
# ---------------------------------------------------------------------------
def test_t3_cache_stampede_parity(monkeypatch):
    """20 righe con un solo cache_key. Grazie al grouping 1 task : 1 cache_key
    entrambi i path fanno UNA chiamata API. Il test presidia il contratto:
    se l'architettura di unique_groups cambiasse, la regressione emergerebbe."""
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "10")
    df = pd.DataFrame({"q": ["same"] * 20})

    sync_call_count = [0]
    sync_call_lock = threading.Lock()

    def sync_fake(params, row_index):
        with sync_call_lock:
            sync_call_count[0] += 1
        # Piccola attesa per esporre la race se esiste.
        import time as _t
        _t.sleep(0.05)
        return {"result": "ok"}, None

    sync_exec = _make_executor()
    _install_fake_call(sync_exec, sync_fake)
    _run_sync(sync_exec, df)
    assert sync_call_count[0] == 1, (
        f"Sync attesa 1 chiamata (dedup + double-checked locking), "
        f"ottenuto {sync_call_count[0]}"
    )

    async_call_count = [0]
    async_call_lock = threading.Lock()

    def async_fake(params, row_index):
        with async_call_lock:
            async_call_count[0] += 1
        import time as _t
        _t.sleep(0.05)
        return {"result": "ok"}, None

    async_exec = _make_executor()
    _install_fake_call(async_exec, async_fake)
    _run_async(async_exec, df)
    assert async_call_count[0] == 1, (
        f"Async attesa 1 chiamata single-flight, ottenuto "
        f"{async_call_count[0]} → stampede attivo"
    )


# ---------------------------------------------------------------------------
# T4 — Rate limiter (HARDENING: rosso pre-fix, verde post-fix)
# ---------------------------------------------------------------------------
def test_t4_rate_limiter_invoked_parity(monkeypatch):
    """Il rate limiter e' parte del contratto enrichment: ogni chiamata API
    deve passare per RateLimiter.acquire(). Sync lo rispetta. Async pre-fix
    non lo rispetta. Post-fix si."""
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    # Sync
    sync_exec = _make_executor(enable_deduplication=False)
    _install_fake_call(sync_exec, fake_call)
    sync_acquire_count = [0]
    sync_lock = threading.Lock()
    original_sync_acquire = sync_exec._rate_limiter.acquire

    def sync_counter():
        with sync_lock:
            sync_acquire_count[0] += 1
        original_sync_acquire()

    sync_exec._rate_limiter.acquire = sync_counter
    _run_sync(sync_exec, df)

    # Async
    async_exec = _make_executor(enable_deduplication=False)
    _install_fake_call(async_exec, fake_call)
    async_acquire_count = [0]
    async_lock = threading.Lock()
    original_async_acquire = async_exec._rate_limiter.acquire

    def async_counter():
        with async_lock:
            async_acquire_count[0] += 1
        original_async_acquire()

    async_exec._rate_limiter.acquire = async_counter
    _run_async(async_exec, df)

    assert sync_acquire_count[0] == 10, (
        f"Sync attesa 10 acquire() (una per chiamata), "
        f"ottenuto {sync_acquire_count[0]}"
    )
    assert async_acquire_count[0] == 10, (
        f"Async attesa 10 acquire() (parity con sync), "
        f"ottenuto {async_acquire_count[0]} → rate limiter bypassato"
    )


# ---------------------------------------------------------------------------
# T5 — skip_null_params (parity stabile)
# ---------------------------------------------------------------------------
def test_t5_skip_null_params_parity(monkeypatch):
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": ["a", None, "b", "", "c"]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    sync_exec = _make_executor()
    _install_fake_call(sync_exec, fake_call)
    _, sync_stats, _ = _run_sync(sync_exec, df)

    async_exec = _make_executor()
    _install_fake_call(async_exec, fake_call)
    _, async_stats, _ = _run_async(async_exec, df)

    # 2 righe null (None e "")
    assert sync_stats.skipped == 2
    assert async_stats.skipped == 2


# ---------------------------------------------------------------------------
# T6 — Error granularity (HARDENING: rosso pre-fix, verde post-fix)
# ---------------------------------------------------------------------------
def test_t6_error_granularity_parity(monkeypatch):
    """20 righe con cache_key identico, errore dall'API con http_status
    e retry_count. Sync replica RowError per ogni riga del gruppo (20).
    Async pre-fix ritorna UN RowError per il gruppo (1). Post-fix 20."""
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": ["same"] * 20})

    def fake_call(params, row_index):
        return None, RowError(
            row_index=row_index,
            status=RowStatus.ERROR,
            error_message="API 503",
            error_type="http_5xx",
            http_status=503,
            retry_count=3,
        )

    sync_exec = _make_executor()
    _install_fake_call(sync_exec, fake_call)
    _, sync_stats, sync_errors = _run_sync(sync_exec, df)

    async_exec = _make_executor()
    _install_fake_call(async_exec, fake_call)
    _, async_stats, async_errors = _run_async(async_exec, df)

    # Stats: entrambi contano 20 errori
    assert sync_stats.errors == 20
    assert async_stats.errors == 20

    # errors list: un entry per riga del gruppo in entrambi i path
    assert len(sync_errors) == 20, (
        f"Sync attesa 20 RowError (uno per riga del gruppo), "
        f"ottenuto {len(sync_errors)}"
    )
    assert len(async_errors) == 20, (
        f"Async attesa 20 RowError (parity con sync), "
        f"ottenuto {len(async_errors)} → errors granularity persa"
    )

    # http_status e retry_count preservati in entrambi
    assert all(e.http_status == 503 for e in sync_errors)
    assert all(e.retry_count == 3 for e in sync_errors)
    assert all(e.http_status == 503 for e in async_errors)
    assert all(e.retry_count == 3 for e in async_errors)


# ---------------------------------------------------------------------------
# T7 — Early stop: divergenza accettata (documentativo)
# ---------------------------------------------------------------------------
def test_t7_early_stop_accepted_divergence(monkeypatch):
    """Async implementa early stop su stop_on_error_threshold a granularita'
    chunk (CHUNK=1000). Sync non ha early stop: processa sempre tutte le
    righe. Documenta il comportamento atteso di ciascun path, non parity."""
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def make_fake():
        count = [0]

        def fake_call(params, row_index):
            count[0] += 1
            return None, RowError(
                row_index=row_index,
                status=RowStatus.ERROR,
                error_message="boom",
                error_type="test",
                http_status=500,
                retry_count=0,
            )

        return fake_call, count

    # Sync: processa tutte le 10 righe indipendentemente dalla soglia
    sync_fake, sync_count = make_fake()
    sync_exec = _make_executor(
        enable_deduplication=False,
        stop_on_error_threshold=3,
        continue_on_error=False,
    )
    _install_fake_call(sync_exec, sync_fake)
    _, sync_stats, _ = _run_sync(sync_exec, df)
    assert sync_count[0] == 10, "sync non implementa early stop"
    assert sync_stats.errors == 10

    # Async: con df piccolo (< CHUNK=1000), l'early stop non si attiva
    # entro il primo chunk. Il comportamento early-stop e' osservabile solo
    # su df > 1000 righe → qui documentiamo che anche async processa tutto.
    async_fake, async_count = make_fake()
    async_exec = _make_executor(
        enable_deduplication=False,
        stop_on_error_threshold=3,
        continue_on_error=False,
    )
    _install_fake_call(async_exec, async_fake)
    _, async_stats, _ = _run_async(async_exec, df)
    assert async_count[0] == 10, "async early stop non si attiva dentro chunk"


# ---------------------------------------------------------------------------
# T8 — Progress callback: divergenza accettata (documentativo)
# ---------------------------------------------------------------------------
def test_t8_progress_callback_accepted_divergence(monkeypatch):
    """Sync invoca progress_callback ad ogni gruppo completato via
    _update_progress (signature: (percent, message)). Async invoca
    progress_callback solo ogni 100 righe e con signature (processed, total).
    Due contratti diversi per design. Documenta il comportamento."""
    monkeypatch.setenv("ENRICHMENT_POOL_SIZE", "5")
    df = pd.DataFrame({"q": [f"v{i}" for i in range(10)]})

    def fake_call(params, row_index):
        return {"result": "ok"}, None

    sync_calls: List[Tuple[Any, ...]] = []

    def sync_cb(*args, **kwargs):
        sync_calls.append(args)

    sync_exec = _make_executor(
        enable_deduplication=False,
        progress_callback=sync_cb,
    )
    _install_fake_call(sync_exec, fake_call)
    _run_sync(sync_exec, df)
    # Sync chiama callback almeno una volta per gruppo completato.
    assert len(sync_calls) >= 1, "sync deve invocare progress_callback"

    async_calls: List[Tuple[Any, ...]] = []

    def async_cb(*args, **kwargs):
        async_calls.append(args)

    async_exec = _make_executor(
        enable_deduplication=False,
        progress_callback=async_cb,
    )
    _install_fake_call(async_exec, fake_call)
    _run_async(async_exec, df)
    # Async con 10 righe: la guardia "% 100 == 0" non scatta mai
    # → zero chiamate. Comportamento documentato.
    assert len(async_calls) == 0, (
        "async invoca progress solo ogni 100 righe (con 10 → 0 callback)"
    )
