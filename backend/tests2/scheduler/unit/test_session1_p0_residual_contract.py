"""Sessione 1 contract test: 4 P0 backend residui dal 2° audit sweep.

Bug coperti:
1. orchestration P0 `concurrency_manager.refresh_lock` TOCTOU — UPDATE
   non-condizionale dopo `_get` con side-effect.
2. orchestration P0 `beat_scheduler` scheduled_for cached al sync-time
   invece di tick-time → idempotency CARICA inaffidabile.
3. data-layer P0 `result_reader.get_result_as_tempfile` tempfile leak
   senza cleanup deterministico.
4. data-layer P0 `db.py.get_latest_result_row` SELECT senza
   `storage_key` → result S3-backed illeggibili.

Pattern Go-friendly: spy/inspection puri, no infra DB richiesta
(eccetto check architettonico via `inspect.getsource`).
"""

from __future__ import annotations

import inspect

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.regression]


# Path 1: refresh_lock atomico

def test_refresh_lock_uses_atomic_update_with_expires_at_guard():
    """`refresh_lock` deve eseguire UPDATE atomico con WHERE
    `expires_at > now()` invece di check `_get` + UPDATE non-condizionale.
    Anti-regression TOCTOU."""
    from app.core.scheduler import concurrency_manager

    src = inspect.getsource(concurrency_manager.ConcurrencyManager.refresh_lock)

    # Stripping commenti per evitare false positive sulle frasi "Pre-fix"
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: no `_get(lock_key)` come check upstream
    # (sostituito da UPDATE atomico)
    assert "self._get(lock_key)" not in src_no_comments, (
        "refresh_lock NON deve fare `_get` upstream del UPDATE — il check "
        "introduce TOCTOU. Usa UPDATE atomico con WHERE expires_at > now()."
    )

    # UPDATE deve avere WHERE su expires_at > now()
    assert "expires_at > now()" in src_no_comments, (
        "refresh_lock UPDATE deve avere `WHERE expires_at > now()` per "
        "garantire atomicita' del check-and-set."
    )


# Path 2: beat_scheduler NON cachea scheduled_for

def test_beat_scheduler_does_not_cache_scheduled_for_in_kwargs():
    """beat_scheduler.py deve passare solo `schedule_id` + `triggered_by`
    nei kwargs della ScheduleEntry, NON `scheduled_for`. Quest'ultimo
    deve essere determinato dal task wrapper a tick-time.
    Pre-fix: `**scheduled_run_kwargs(triggered_by="cron")` pre-calcolava
    scheduled_for=now() al sync (ogni 60s) e lo cachava in entry.kwargs;
    Celery riusava lo stesso valore ad ogni tick → idempotency CARICA
    key restava costante → tick consecutivi SKIPPED_IDEMPOTENT."""
    from workers import beat_scheduler

    src = inspect.getsource(beat_scheduler)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Anti-regression: lo splatting di scheduled_run_kwargs(triggered_by=...)
    # non deve apparire nei kwargs di ScheduleEntry (sarebbe il bug)
    assert "**scheduled_run_kwargs(triggered_by=\"cron\")" not in src_no_comments, (
        "beat_scheduler NON deve splattare scheduled_run_kwargs nei "
        "kwargs di ScheduleEntry — questo cachea scheduled_for al "
        "sync-time. Usa solo {'schedule_id': sid, 'triggered_by': 'cron'}."
    )


def test_schedule_task_computes_scheduled_for_at_tick_time_for_cron():
    """schedule_pipeline_task riceve scheduled_for=None per i tick cron
    (per design post-fix) e DEVE calcolarlo a tick-time. No warning su
    triggered_by='cron' (e' il path canonico)."""
    from workers.tasks import schedule_task

    src = inspect.getsource(schedule_task.schedule_pipeline_task)

    # Anti-regression: il warning DEVE essere condizionale su
    # triggered_by != "cron". Altrimenti spammava il log ad ogni tick.
    assert 'if triggered_by != "cron"' in src or "triggered_by != 'cron'" in src, (
        "schedule_pipeline_task deve sopprimere il warning 'dispatcher "
        "non canonico' quando triggered_by='cron' (e' il path canonico "
        "post-fix beat_scheduler)."
    )


# Path 3: tempfile cleanup deterministico

def test_get_result_as_tempfile_cm_yields_path_and_cleans_up(tmp_path, monkeypatch):
    """get_result_as_tempfile_cm e' un context manager: cancella il
    tempfile al `with` exit. Pre-fix solo get_result_as_tempfile esisteva,
    senza cleanup → disk fill."""
    from app.core.results import result_reader

    # Mock store.get_latest_result_row + get_result_data per fornire dati
    class _FakeRow:
        id = "r-1"
        format = "csv"

    monkeypatch.setattr(
        "app.core.results.result_reader.store.get_latest_result_row",
        lambda project_id: _FakeRow(),
    )
    monkeypatch.setattr(
        "app.core.results.result_reader.store.get_result_data",
        lambda result_id: b"fake-csv-data",
    )
    monkeypatch.setattr(
        "app.core.results.result_reader.make_cached_result_path",
        lambda suffix: tmp_path / f"result{suffix}",
    )

    written_path = None
    with result_reader.get_result_as_tempfile_cm("proj-1") as p:
        assert p is not None
        assert p.exists()
        written_path = p

    # ANTI-REGRESSION: il file DEVE essere cancellato al with-exit
    assert not written_path.exists(), (
        "get_result_as_tempfile_cm NON ha cancellato il tempfile al "
        "with-exit. Senza cleanup, l'uso del context manager non "
        "elimina il leak."
    )


def test_get_result_as_tempfile_deprecated_warning_emitted(tmp_path, monkeypatch):
    """get_result_as_tempfile (deprecated) emette DeprecationWarning."""
    import warnings
    from app.core.results import result_reader

    monkeypatch.setattr(
        "app.core.results.result_reader.store.get_latest_result_row",
        lambda project_id: None,  # nessun result
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result_reader.get_result_as_tempfile("proj-1")
        deprecation_warnings = [w for w in captured if issubclass(w.category, DeprecationWarning)]

    assert any(
        "get_result_as_tempfile_cm" in str(w.message)
        for w in deprecation_warnings
    ), (
        "get_result_as_tempfile deve emettere DeprecationWarning che "
        "indirizza al context manager"
    )


# Path 4: get_latest_result_row include storage_key

def test_get_latest_result_row_select_includes_storage_key():
    """`get_latest_result_row` SELECT deve includere `storage_key`.
    Pre-fix: SELECT senza la colonna → result S3-backed avevano
    storage_key=None nel ResultMetadataRow → loader downstream
    pensava fossero BYTEA → ritornava None silenziosamente."""
    from app.core.results import db as result_db

    src = inspect.getsource(result_db.get_latest_result_row)
    src_no_comments = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )

    # Conta le occorrenze di "storage_key" nel SQL — deve apparire
    # in entrambi i branch (group_id specificato / no group_id)
    assert src_no_comments.count("storage_key") >= 2, (
        "get_latest_result_row deve includere `storage_key` nella SELECT "
        "in entrambi i branch (con/senza group_id). Mancanza causava "
        "result S3-backed illeggibili."
    )
