"""
Bounded-bytes contract for ``ResultBlobLoader``.

BE-GO-READY-RESOURCE-ENGINE-010-RESULT-BLOB-BOUNDED-BYTES.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests2.shared._source_scan import python_code_only as _python_code_only
from tests2.shared._resource_loader_helpers import (
    fake_fetchone,
    make_result_ref,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


_REPO_ROOT = Path(__file__).resolve().parents[3]
_BACKEND_ROOT = _REPO_ROOT / "backend"
_RESULT_BLOB_PY = (
    _BACKEND_ROOT / "app" / "core" / "resources" / "loaders" / "result_blob.py"
)


# 1. Cap resolution


def test_default_cap_is_200_mib():
    """Default cap mirrors ``RESOURCE_MAX_BYTES`` (200 MiB post
    BE-GO-READY-MEMORY-ENGINE-005). BE-GO-READY-MEMORY-ENGINE-007
    removed the loader's compat alias: tests now call the canonical
    ``resolve_resource_max_bytes`` directly."""
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        RESOURCE_MAX_BYTES_ENV,
        resolve_resource_max_bytes,
    )

    # Clear any test-side env override before resolving.
    prior = os.environ.pop(RESOURCE_MAX_BYTES_ENV, None)
    try:
        assert resolve_resource_max_bytes() == 200 * 1024 * 1024
        assert RESOURCE_MAX_BYTES_DEFAULT == 200 * 1024 * 1024
    finally:
        if prior is not None:
            os.environ[RESOURCE_MAX_BYTES_ENV] = prior


def test_env_override_takes_effect_at_call_time(monkeypatch):
    """Setting ``RESOURCE_MAX_BYTES`` overrides the default; the
    read happens per-call so a runtime mutation is immediately
    observed."""
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_ENV,
        resolve_resource_max_bytes,
    )

    monkeypatch.setenv(RESOURCE_MAX_BYTES_ENV, "1024")
    assert resolve_resource_max_bytes() == 1024


@pytest.mark.parametrize("bad_value", ["", "not-a-number", "0", "-1"])
def test_invalid_or_nonpositive_env_falls_back_to_default(
    monkeypatch, bad_value
):
    """Empty / non-int / non-positive values silently fall back to
    the default. A misconfigured env must NOT silently disable the
    cap."""
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        RESOURCE_MAX_BYTES_ENV,
        resolve_resource_max_bytes,
    )

    monkeypatch.setenv(RESOURCE_MAX_BYTES_ENV, bad_value)
    assert resolve_resource_max_bytes() == RESOURCE_MAX_BYTES_DEFAULT


# 2. Bounded reader unit-level


def test_read_bounded_stream_assembles_bytes_within_cap():
    """Under the cap: full assembly works; iterator is exhausted."""
    from app.core.resources.bounded_reader import read_stream_bounded

    chunks = [b"abc", b"de", b"fg"]
    out = read_stream_bounded(
        iter(chunks),
        max_bytes=10,
        label="r.bin",
        stage="result_blob_oversize",
    )
    assert out == b"abcdefg"


def test_read_bounded_stream_aborts_iteration_on_oversize():
    """The iterator MUST be aborted on cap-exceeding chunk; no
    further chunks read. Verified by a tracking iterator that
    records every yield."""
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.bounded_reader import read_stream_bounded

    yielded: list[bytes] = []

    def _tracking_iter():
        for c in (b"x" * 50, b"y" * 60, b"z" * 999_999):
            yielded.append(c)
            yield c

    with pytest.raises(MemoryLimitExceededError) as exc_info:
        read_stream_bounded(
            _tracking_iter(),
            max_bytes=100,
            label="r.bin",
            stage="result_blob_oversize",
        )

    # The third chunk MUST NOT have been yielded -- iteration
    # aborted at the second.
    assert yielded == [b"x" * 50, b"y" * 60]
    err = exc_info.value
    # observed_bytes is the SIZE THAT TRIGGERED the abort, not the
    # full blob size (we never read it).
    assert err.observed_bytes == 110
    assert err.limit_bytes == 100
    assert err.stage == "result_blob_oversize"


def test_read_bounded_stream_exact_match_succeeds():
    """Exactly-equal-to-cap is allowed (boundary inclusive)."""
    from app.core.resources.bounded_reader import read_stream_bounded

    out = read_stream_bounded(
        iter([b"x" * 100]),
        max_bytes=100,
        label="result",
        stage="result_blob_oversize",
    )
    assert out == b"x" * 100


def test_read_bounded_stream_skips_empty_chunks():
    """Empty chunks (some clients return them as keepalives) are
    passed through harmlessly without consuming cap budget."""
    from app.core.resources.bounded_reader import read_stream_bounded

    out = read_stream_bounded(
        iter([b"", b"abc", b"", b"def", b""]),
        max_bytes=10,
        label="result",
        stage="result_blob_oversize",
    )
    assert out == b"abcdef"


# 3. Object-storage end-to-end: oversize aborts iteration


class _TrackingBody:
    """Stand-in for the iterator returned by ``get_object_stream``.

    Records every chunk that has been yielded, so a test can prove
    the loader did NOT consume the iterator past the abort point.
    """

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.yielded: list[bytes] = []

    def __iter__(self):
        for c in self._chunks:
            self.yielded.append(c)
            yield c


class _FakeStorage:
    def __init__(self, body):
        self._body = body

    def get_object_stream(self, key):
        return {"body": self._body}


def test_object_storage_oversize_raises_without_consuming_full_iterator(
    monkeypatch,
):
    """End-to-end: the loader's ``mode='bytes'`` path raises
    :class:`MemoryLimitExceededError` and the iterator is aborted
    BEFORE the full blob is consumed. Pre-010 the entire iterator
    was drained via ``list(stream["body"])`` then ``b"".join``ed
    -- the OOM had already happened by the time any size check
    could run.
    """
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders import result_blob as loader_mod

    # Cap = 100 bytes; emit two 60-byte chunks then a "huge" chunk
    # that the loader MUST NOT touch.
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "100")
    body = _TrackingBody([b"a" * 60, b"b" * 60, b"c" * 10_000_000])
    fake_storage = _FakeStorage(body)

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )
    _, fake_fetch = fake_fetchone(
        row={"data": None, "storage_key": "results/p/oversize.bin"}
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    ref = make_result_ref()
    loader = loader_mod.ResultBlobLoader()
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        loader.load(ref, mode="bytes")

    # Critical: the third chunk (the "huge" one) MUST NOT have
    # been yielded. The iterator stopped at chunk #2 (60+60>100).
    assert len(body.yielded) == 2, (
        "Loader consumed more chunks than necessary; the bounded "
        "read MUST abort at the first cap-exceeding chunk. "
        f"yielded chunk sizes: {[len(c) for c in body.yielded]}"
    )
    err = exc_info.value
    assert err.limit_bytes == 100
    assert err.observed_bytes == 120
    assert err.stage == "result_blob_oversize"


def test_object_storage_under_cap_returns_full_bytes(monkeypatch):
    """Sanity: when the blob fits, the loader assembles it correctly
    (regression cover for the read-loop refactor)."""
    from app.core.resources.loaders import result_blob as loader_mod

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")
    body = _TrackingBody([b"abc", b"de", b"fg"])
    fake_storage = _FakeStorage(body)

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )
    _, fake_fetch = fake_fetchone(
        row={"data": None, "storage_key": "results/p/r.bin"}
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    ref = make_result_ref()
    out = loader_mod.ResultBlobLoader().load(ref, mode="bytes")

    assert out.data == b"abcdefg"
    assert out.size_bytes == 7
    # Iterator fully consumed because it fit.
    assert body.yielded == [b"abc", b"de", b"fg"]


def test_object_storage_oversize_does_not_swallow_to_none(monkeypatch):
    """Pre-010 every exception in `_download_object_storage` was
    coerced to ``None``. Post-010 the cap signal MUST NOT be
    coerced -- callers learn about the size cap, not a
    "blob unavailable" decoy."""
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders import result_blob as loader_mod

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "10")
    body = _TrackingBody([b"x" * 100])
    fake_storage = _FakeStorage(body)
    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: fake_storage,
    )

    with pytest.raises(MemoryLimitExceededError):
        loader_mod._download_object_storage("results/p/o.bin")


def test_object_storage_connectivity_error_still_returns_none(monkeypatch):
    """Negative cover: connectivity failure (NOT oversize) keeps
    its pre-010 ``None`` semantics so callers can distinguish
    'blob unavailable' from 'blob too big'."""
    from app.core.resources.loaders import result_blob as loader_mod

    class _Boom:
        def get_object_stream(self, key):
            raise RuntimeError("simulated network outage")

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: _Boom(),
    )
    out = loader_mod._download_object_storage("results/p/r.bin")
    assert out is None


# 4. Legacy BYTEA path retired by the object-storage-only cutover.


def test_result_row_without_storage_key_fails_closed(monkeypatch):
    from app.core.resources.loaders import result_blob as loader_mod

    _, fake_fetch = fake_fetchone(
        row={"storage_key": None, "project_id": "project-1"}
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    with pytest.raises(RuntimeError, match="senza storage_key"):
        loader_mod.ResultBlobLoader().load(make_result_ref(), mode="bytes")


@pytest.mark.skip(reason="BYTEA result payloads removed; see fail-closed storage_key contract")
def test_bytea_oversize_raises_at_dispatch(monkeypatch):
    """An oversized BYTEA row raises MemoryLimitExceededError
    BEFORE the bytes ever reach Python. Post-013 the SQL
    ``CASE WHEN length(data) > %s THEN NULL ELSE data END``
    nullifies ``data`` server-side; the loader uses the
    projected ``data_size`` column to raise. The mock returns
    ``data=None`` (mirroring the SQL CASE result) and
    ``data_size=100`` (mirroring the ``length(data)`` projection).
    """
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders import result_blob as loader_mod

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "50")
    # 013: `data` is NULL on the wire when length(data) > cap; the
    # loader reads `data_size` to detect oversize, so the mock must
    # carry both fields the production SQL projects.
    _, fake_fetch = fake_fetchone(
        row={"data": None, "data_size": 100, "storage_key": None}
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    ref = make_result_ref()
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        loader_mod.ResultBlobLoader().load(ref, mode="bytes")

    err = exc_info.value
    assert err.limit_bytes == 50
    assert err.observed_bytes == 100, (
        "observed_bytes must be the SQL `length(data)` value, not "
        "len(data) (data is None when preflight fires)"
    )
    assert err.stage == "result_blob_oversize"


@pytest.mark.skip(reason="BYTEA result payloads removed; see fail-closed storage_key contract")
def test_bytea_preflight_does_not_touch_data_field(monkeypatch):
    """013 contract: the loader's oversize decision is driven by
    ``data_size`` from the SQL projection, NOT by ``len(data)``.
    Even a mock that puts a sentinel object into ``data`` (which
    would crash any ``len()`` call) MUST still raise cleanly,
    proving the loader never reads ``data`` on the oversize path.
    """
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders import result_blob as loader_mod

    class _ExplodingSentinel:
        """If anything calls len() / iterates / casts this, the
        test fails loudly. Stand-in for a row whose `data` field
        the loader MUST NOT touch on the oversize path."""

        def __len__(self):
            raise AssertionError(
                "loader read `data` on the oversize path; the 013 "
                "preflight contract requires that `data_size` alone "
                "drives the cap decision"
            )

        def __iter__(self):
            raise AssertionError("loader iterated `data` on oversize")

        def __bytes__(self):
            raise AssertionError("loader bytes()'d `data` on oversize")

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "50")
    _, fake_fetch = fake_fetchone(
        row={
            "data": _ExplodingSentinel(),
            "data_size": 999,  # >> cap, preflight must fire
            "storage_key": None,
        }
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    ref = make_result_ref()
    with pytest.raises(MemoryLimitExceededError) as exc_info:
        loader_mod.ResultBlobLoader().load(ref, mode="bytes")

    assert exc_info.value.observed_bytes == 999
    assert exc_info.value.limit_bytes == 50


@pytest.mark.skip(reason="BYTEA result payloads removed; SQL now selects metadata only")
def test_bytea_preflight_sql_carries_length_and_case(monkeypatch):
    """013 wire-level contract: the SQL fired by the loader MUST
    contain the ``length(data)`` projection AND a CASE that NULLs
    out ``data`` when ``length(data) > ?``. Without this shape,
    PG would still ship the oversized BYTEA back to Python before
    the loader could check the size."""
    from app.core.resources.loaders import result_blob as loader_mod

    captured: dict = {}

    def _capture(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return None  # row-not-found is fine for SQL-shape inspection

    monkeypatch.setattr("app.db.pg_connection.fetchone", _capture)
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "12345")

    ref = make_result_ref()
    with pytest.raises(FileNotFoundError):
        loader_mod.ResultBlobLoader().load(ref, mode="bytes")

    sql = captured["sql"]
    # length(data) projection (data_size column) — required for
    # caller-side cap enforcement.
    assert "length(data) AS data_size" in sql, (
        "SQL must project `length(data) AS data_size` so the loader "
        "can raise on oversize without touching the bytes"
    )
    # CASE-bounded data column — required for wire-level preflight.
    assert "CASE" in sql and "WHEN length(data) > %s" in sql, (
        "SQL must NULL `data` via CASE WHEN length(data) > %s, "
        "otherwise PG ships the oversized BYTEA over the wire"
    )
    assert "ELSE data" in sql, (
        "SQL CASE must fall through to `data` only when under cap"
    )
    # max_bytes is the leading bound parameter (CASE placeholder
    # appears before any WHERE-clause placeholder).
    assert captured["params"][0] == 12345


@pytest.mark.skip(reason="BYTEA result payloads removed; object storage is mandatory")
def test_bytea_under_cap_returns_normally(monkeypatch):
    """Sanity: BYTEA payloads under the cap pass through unchanged.
    Production SQL CASE returns the actual ``data`` and the
    ``data_size`` projection mirrors ``len(data)``; the loader
    skips the preflight branch and yields the bytes verbatim."""
    from app.core.resources.loaders import result_blob as loader_mod

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")
    _, fake_fetch = fake_fetchone(
        row={"data": b"small", "data_size": 5, "storage_key": None}
    )
    monkeypatch.setattr("app.db.pg_connection.fetchone", fake_fetch)

    ref = make_result_ref()
    out = loader_mod.ResultBlobLoader().load(ref, mode="bytes")
    assert out.data == b"small"


# 5. Path mode rimossa post-Phase5: tombstone


def test_path_mode_rejected_unconditionally():
    """`mode='path'` rimosso (zero production caller). Il loader rifiuta
    esplicitamente: chi necessita di un path filesystem usa
    `app.core.io.materialize.temporary_path_from_bytes` come escape hatch."""
    from app.core.resources.loaders import result_blob as loader_mod

    loader = loader_mod.ResultBlobLoader()
    # Test che mode='path' raise PRIMA di qualunque DB/storage access:
    # ref senza configurazione DB/storage e' OK (validation fail-closed
    # alla mode-check, prima del dispatch). Per safety usiamo make_result_ref
    # con un result_id presente (non None).
    ref = make_result_ref()
    with pytest.raises(ValueError, match="not supported"):
        loader.load(ref, mode="path")  # type: ignore[arg-type]


# 6. Stream mode honesty: still unsupported, no body read


def test_stream_mode_raises_before_any_storage_access(monkeypatch):
    """``mode='stream'`` MUST fail-closed BEFORE any DB or storage
    access. A test that sets up tripwires on both layers proves the
    short-circuit. (010 explicitly chose 'unsupported, no body
    read' over a half-baked stream implementation.)"""
    from app.core.resources.loaders import result_blob as loader_mod

    fetch_calls: list[tuple] = []

    def _tripwire_fetchone(sql, params=()):
        fetch_calls.append((sql, params))
        return {"data": None, "storage_key": "results/p/r.bin"}

    monkeypatch.setattr("app.db.pg_connection.fetchone", _tripwire_fetchone)

    storage_calls: list[str] = []

    class _Tripwire:
        def get_object_stream(self, key):
            storage_calls.append(key)
            raise AssertionError("get_object_stream must NOT be called")

    monkeypatch.setattr(
        "app.infrastructure.storage.cloud_backends.get_runtime_object_storage",
        lambda: _Tripwire(),
    )

    ref = make_result_ref()
    with pytest.raises(ValueError, match="not supported"):
        loader_mod.ResultBlobLoader().load(ref, mode="stream")

    assert fetch_calls == [], (
        "stream mode must reject BEFORE any DB SELECT"
    )
    assert storage_calls == [], (
        "stream mode must reject BEFORE any object storage access"
    )


# 7. Source-bound: the unbounded `b"".join(list(...))` pattern is gone


def test_source_no_longer_contains_unbounded_join_pattern():
    """Lock the regression: the pre-010 pattern
    ``chunks = list(stream["body"]); data = b"".join(chunks)`` is
    structurally banned in EXECUTABLE code. Re-introducing it
    (or any ``list(stream[...])`` over the body iterator) must
    fail at CI time. The audit comment / docstring is allowed to
    mention it as historical context (strings + comments are
    stripped before the scan)."""
    text = _RESULT_BLOB_PY.read_text(encoding="utf-8")
    code_only = _python_code_only(text)

    forbidden_patterns = (
        "chunks = list ( stream",
        "list ( stream ",
        # The classic "consume the whole iterator into a list"
        # pattern. The bounded reader uses an explicit for-loop.
    )
    offenders = [p for p in forbidden_patterns if p in code_only]
    assert not offenders, (
        f"app/core/resources/loaders/result_blob.py contains "
        f"unbounded-read pattern(s) in executable code: {offenders}. "
        "The bounded reader (`read_stream_bounded`) must own all "
        "body iteration."
    )


def test_loader_module_does_not_import_app_config_after_010():
    """Confirms the 004 anti-god-engine invariant survives 010:
    the cap is read from ``os.environ``, NOT from
    ``app.config.settings``. A future refactor that promotes the
    cap into Settings (Worker 6) MUST keep the loader's import
    edge clean by routing through stdlib env-var resolution
    (or a new pure helper that itself does not import Settings)."""
    text = _RESULT_BLOB_PY.read_text(encoding="utf-8")

    forbidden = ("from app.config", "import app.config")
    offenders = [n for n in forbidden if n in text]
    assert not offenders, (
        f"app/core/resources/loaders/result_blob.py imports "
        f"{offenders}: anti-god-engine."
    )
