"""
Contract test BE-GO-READY-TRANSFORMATION-ENGINE-012-WRITER-BATCH-LOOP-EXTRACT.

Locks the boundary contract of the new helper
"""

from __future__ import annotations

import os

# Ensure DATABASE_URL set before any pg_connection import.
os.environ.setdefault(
    "DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test"
)

import time
from pathlib import Path
from typing import List, Tuple

import pandas as pd
import pytest

from app.core.carica.models import BatchProgress, WriteResult
from app.core.carica.writer_batch_loop import run_batched_inserts


pytestmark = [pytest.mark.unit, pytest.mark.contract]


def _make_result(total_rows: int) -> WriteResult:
    """Build a minimal WriteResult skeleton suitable for the helper."""
    return WriteResult(
        started_at="2026-05-05T00:00:00+00:00",
        total_rows=total_rows,
        table_name="test.tbl",
        write_mode="append",
    )


# 1. Slicing + iteration count


def test_run_batched_inserts_slices_df_per_batch_size():
    """Each batch must receive `df.iloc[start:end]` with start =
    batch_num*batch_size and end = min(start+batch_size, len(df))."""
    df = pd.DataFrame({"v": list(range(25))})
    result = _make_result(total_rows=25)
    seen: List[Tuple[List[int], int]] = []

    def _process(batch_df, batch_num):
        seen.append((batch_df["v"].tolist(), batch_num))

    run_batched_inserts(
        df=df, result=result, batch_size=10, total_batches=3,
        start_time=time.time(),
        process_batch=_process, report_progress=lambda _p: None,
    )

    assert seen == [
        (list(range(0, 10)), 0),
        (list(range(10, 20)), 1),
        (list(range(20, 25)), 2),
    ]


def test_run_batched_inserts_total_batches_zero_is_noop():
    """`total_batches=0` (e.g. empty DataFrame) ⇒ helper does not
    iterate, does not emit progress."""
    df = pd.DataFrame({"v": []})
    result = _make_result(total_rows=0)
    process_calls: List[int] = []
    progress_calls: List[BatchProgress] = []

    run_batched_inserts(
        df=df, result=result, batch_size=10, total_batches=0,
        start_time=time.time(),
        process_batch=lambda _b, _n: process_calls.append(_n),
        report_progress=lambda p: progress_calls.append(p),
    )

    assert process_calls == []
    assert progress_calls == []


def test_run_batched_inserts_invokes_process_batch_total_batches_times():
    """Callback invoked exactly `total_batches` times (even if some
    batches turn out shorter)."""
    df = pd.DataFrame({"v": list(range(7))})
    result = _make_result(total_rows=7)
    invocations: List[int] = []

    run_batched_inserts(
        df=df, result=result, batch_size=3, total_batches=3,
        start_time=time.time(),
        process_batch=lambda _b, n: invocations.append(n),
        report_progress=lambda _p: None,
    )

    assert invocations == [0, 1, 2]


# 2. Result mutation owned by callback (helper is read-only on result)


def test_helper_does_not_mutate_result_directly():
    """The helper itself does not write to `result.rows_written` /
    `rows_failed` / `batches_completed`. Mutation belongs to the
    callback."""
    df = pd.DataFrame({"v": list(range(6))})
    result = _make_result(total_rows=6)
    rows_written_before = result.rows_written
    batches_completed_before = result.batches_completed

    # Callback that does NOT mutate result.
    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=3,
        start_time=time.time(),
        process_batch=lambda _b, _n: None,
        report_progress=lambda _p: None,
    )

    assert result.rows_written == rows_written_before
    assert result.batches_completed == batches_completed_before


def test_callback_can_mutate_result_per_batch():
    """A typical callback updates `result.rows_written` /
    `result.batches_completed` per batch."""
    df = pd.DataFrame({"v": list(range(10))})
    result = _make_result(total_rows=10)

    def _process(batch_df, batch_num):
        result.rows_written += len(batch_df)
        result.batches_completed = batch_num + 1

    run_batched_inserts(
        df=df, result=result, batch_size=3, total_batches=4,
        start_time=time.time(),
        process_batch=_process, report_progress=lambda _p: None,
    )

    assert result.rows_written == 10
    assert result.batches_completed == 4


# 3. Progress reporting


def test_progress_emitted_once_per_batch_after_process_batch():
    """progress is emitted AFTER process_batch (so it reflects the
    callback's mutation of result.rows_written)."""
    df = pd.DataFrame({"v": list(range(6))})
    result = _make_result(total_rows=6)
    timeline: List[str] = []

    def _process(batch_df, batch_num):
        result.rows_written += len(batch_df)
        timeline.append(f"process:{batch_num}:rw={result.rows_written}")

    def _progress(payload: BatchProgress):
        timeline.append(
            f"progress:{payload.batch_number}:rw={payload.rows_written}"
        )

    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=3,
        start_time=time.time(),
        process_batch=_process, report_progress=_progress,
    )

    assert timeline == [
        "process:0:rw=2", "progress:1:rw=2",
        "process:1:rw=4", "progress:2:rw=4",
        "process:2:rw=6", "progress:3:rw=6",
    ]


def test_progress_payload_fields_match_canonical_shape():
    """BatchProgress fields: batch_number (1-indexed), total_batches,
    rows_written (current), total_rows (from result), elapsed_seconds."""
    df = pd.DataFrame({"v": list(range(4))})
    result = _make_result(total_rows=4)
    captured: List[BatchProgress] = []

    def _process(batch_df, batch_num):
        result.rows_written += len(batch_df)

    start = time.time()
    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=2,
        start_time=start,
        process_batch=_process, report_progress=captured.append,
    )

    assert len(captured) == 2
    assert captured[0].batch_number == 1
    assert captured[0].total_batches == 2
    assert captured[0].rows_written == 2
    assert captured[0].total_rows == 4
    assert captured[0].elapsed_seconds >= 0
    assert captured[1].batch_number == 2
    assert captured[1].rows_written == 4
    # elapsed monotonic non-decreasing.
    assert captured[1].elapsed_seconds >= captured[0].elapsed_seconds


def test_elapsed_seconds_relative_to_start_time():
    """elapsed_seconds = time.time() - start_time at emit moment."""
    df = pd.DataFrame({"v": list(range(2))})
    result = _make_result(total_rows=2)
    captured: List[BatchProgress] = []

    fixed_start = time.time() - 5.0  # 5 seconds before now

    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=1,
        start_time=fixed_start,
        process_batch=lambda _b, _n: None,
        report_progress=captured.append,
    )

    assert len(captured) == 1
    # >= 5 because start_time is 5s in the past; allow generous slack.
    assert captured[0].elapsed_seconds >= 5.0
    assert captured[0].elapsed_seconds < 30.0


# 4. Exception propagation


def test_callback_exception_propagates_verbatim():
    """The helper does not catch exceptions from process_batch.
    They propagate to the caller (which owns the outer try/except,
    e.g. `engine.begin()` rollback)."""
    df = pd.DataFrame({"v": list(range(6))})
    result = _make_result(total_rows=6)

    class _Boom(Exception):
        pass

    def _process(batch_df, batch_num):
        if batch_num == 1:
            raise _Boom("fail batch 1")

    progress_calls: List[BatchProgress] = []

    with pytest.raises(_Boom, match="fail batch 1"):
        run_batched_inserts(
            df=df, result=result, batch_size=2, total_batches=3,
            start_time=time.time(),
            process_batch=_process,
            report_progress=progress_calls.append,
        )

    # Batch 0 emitted progress before the failure on batch 1.
    # Batch 1 raised before progress emit. Batch 2 never ran.
    assert len(progress_calls) == 1
    assert progress_calls[0].batch_number == 1


def test_callback_internal_exception_swallow_does_not_break_loop():
    """If a callback handles its own exception (matches
    `_execute_per_batch` semantics), the loop continues."""
    df = pd.DataFrame({"v": list(range(6))})
    result = _make_result(total_rows=6)
    swallowed: List[int] = []

    def _process(batch_df, batch_num):
        try:
            if batch_num == 1:
                raise RuntimeError("transient")
            result.rows_written += len(batch_df)
        except RuntimeError:
            result.rows_failed += len(batch_df)
            swallowed.append(batch_num)

    progress_calls: List[BatchProgress] = []
    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=3,
        start_time=time.time(),
        process_batch=_process,
        report_progress=progress_calls.append,
    )

    assert swallowed == [1]
    assert result.rows_written == 4  # batches 0 + 2
    assert result.rows_failed == 2   # batch 1
    assert len(progress_calls) == 3


# 5. Source-bound boundary: helper purity


_BACKEND_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_HELPER_PY = _BACKEND_ROOT / "app" / "core" / "carica" / "writer_batch_loop.py"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_helper_module_is_pure_no_db_or_settings_imports():
    """Boundary: the helper module must not import DB engine,
    settings, env, FastAPI, or DDL/sqlalchemy execution APIs.
    Forbidden imports = source-bound assertion."""
    text = _read(_HELPER_PY)
    forbidden = [
        "from app.config",
        "import app.config",
        "from app.db.",
        "import app.db.",
        "from sqlalchemy import text",
        "from sqlalchemy.engine import",
        "import os\n",
        "from fastapi",
        "import fastapi",
    ]
    for line in forbidden:
        assert line not in text, (
            f"writer_batch_loop must remain pure (no I/O/DB/settings). "
            f"Forbidden import found: {line!r}"
        )


def test_helper_signature_is_kw_only_with_typed_callables():
    """Signature lock: kw-only (`*` then named params), explicit
    type hints, single function exported."""
    text = _read(_HELPER_PY)
    assert "def run_batched_inserts(" in text
    assert "*," in text  # kw-only marker
    assert "process_batch: Callable[[pd.DataFrame, int], None]" in text
    assert "report_progress: Callable[[BatchProgress], None]" in text
    assert "result: WriteResult" in text
    assert "batch_size: int" in text
    assert "total_batches: int" in text
    assert "start_time: float" in text
    assert "df: pd.DataFrame" in text


def test_helper_is_only_canonical_loop_remaining():
    """Source-bound: zero `for batch_num in range(total_batches)`
    inline loops in writer_transaction_exec.py / writer_replace_strategies.py.
    The only remaining occurrence is in writer_batch_loop.py itself."""
    import re
    pattern = re.compile(r"for\s+batch_num\s+in\s+range\(total_batches\)")
    callsites = (
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_transaction_exec.py",
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_replace_strategies.py",
    )
    for p in callsites:
        text = _read(p)
        m = pattern.search(text)
        assert m is None, (
            f"Inline batch loop still present in {p.name} — "
            f"should have been migrated to run_batched_inserts."
        )

    # And the helper itself owns exactly one canonical loop.
    helper_text = _read(_HELPER_PY)
    matches = pattern.findall(helper_text)
    assert len(matches) == 1


def test_callsites_use_run_batched_inserts():
    """Source-bound: each of the 2 + 4 caller files must contain
    `run_batched_inserts(` calls. Drift = a caller bypassed the
    helper."""
    tx_exec = _read(
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_transaction_exec.py"
    )
    replace = _read(
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_replace_strategies.py"
    )
    oracle_redef = _read(
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_oracle_redef.py"
    )
    # 2 calls in tx_exec (atomic + per_batch).
    assert tx_exec.count("run_batched_inserts(") >= 2
    # 3 generic replace calls remain in replace_strategies.
    assert replace.count("run_batched_inserts(") >= 3
    # Oracle redefinition owns its batch loop in the dedicated module.
    assert oracle_redef.count("run_batched_inserts(") >= 1


def test_oracle_redefinition_sql_lives_in_dedicated_module():
    replace = _read(
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_replace_strategies.py"
    )
    oracle_redef = _read(
        _BACKEND_ROOT / "app" / "core" / "carica" / "writer_oracle_redef.py"
    )

    assert "DBMS_REDEFINITION" not in replace
    assert "def execute_oracle_redefinition(" in oracle_redef
    assert "DBMS_REDEFINITION" in oracle_redef


# 6. target_table forwarding (verified through callback closure)


def test_callback_can_forward_target_table_via_closure():
    """4 of 6 callsites need to pass `target_table` to
    `_execute_batch`. The pattern is a closure that captures the
    target name; this test verifies the helper does not interfere."""
    df = pd.DataFrame({"v": list(range(4))})
    result = _make_result(total_rows=4)
    captured_target: List[str] = []

    def _execute_batch_stub(conn, batch_df, cols, sql, op, *,
                            target_table=None, target_schema=None):
        captured_target.append(target_table)
        return len(batch_df)

    fake_conn = object()
    target = "tbl__stg"

    def _process(batch_df, batch_num):
        rows = _execute_batch_stub(
            fake_conn, batch_df, [], "INSERT", "insert",
            target_table=target,
        )
        result.rows_written += rows

    run_batched_inserts(
        df=df, result=result, batch_size=2, total_batches=2,
        start_time=time.time(),
        process_batch=_process, report_progress=lambda _p: None,
    )

    assert captured_target == ["tbl__stg", "tbl__stg"]
    assert result.rows_written == 4
