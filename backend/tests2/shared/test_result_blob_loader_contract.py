"""Contract tests for `ResultBlobLoader`.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.

Locks the loader's behavior:

  1. fail-closed mode rejection (only `bytes` in v1);
  2. fail-closed kind mismatch;
  3. fail-closed missing result_id;
  4. SQL filter is keyed on group_id when `is_superadmin=False`;
  5. SQL filter is skipped when `is_superadmin=True` (with audit
     log breadcrumb);
  6. dispatch on `storage_key` non-null -> object storage path;
  7. dispatch on `storage_key` null -> PG BYTEA fallback;
  8. memoryview -> bytes conversion preserved (postgres BYTEA);
  9. row not found -> FileNotFoundError (NOT silent None);
  10. back-compat shim `_dispatch_by_id` mirrors the legacy
      `get_result_data(result_id, group_id=None)` semantics.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# ---------------------------------------------------------------------------
# Fail-closed input validation
# ---------------------------------------------------------------------------


def test_load_rejects_wrong_kind():
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    # Construct a ref that's syntactically valid for ResourceRef
    # but whose `kind` is mismatched against the loader. We bypass
    # the Literal type check since the test is about runtime
    # enforcement at the loader, not at type construction.
    ref = ResourceRef(
        kind="result_blob",  # type: ignore[arg-type]
        expected_group_id="g1",
        result_id="r1",
    )
    object.__setattr__(ref, "kind", "file_source_blob")

    loader = ResultBlobLoader()
    with pytest.raises(ValueError, match="does not handle kind"):
        loader.load(ref, mode="bytes")


def test_load_rejects_missing_result_id():
    """A ref that has its kind set to result_blob but no result_id
    cannot reach the loader through normal channels (ResourceRef
    enforces exactly-one-id at __post_init__). The loader still
    asserts it explicitly as defense-in-depth: an internal caller
    that constructed a degenerate ref via object.__setattr__
    (e.g. testing) should fail loudly."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    object.__setattr__(ref, "result_id", None)

    loader = ResultBlobLoader()
    with pytest.raises(ValueError, match="result_id required"):
        loader.load(ref, mode="bytes")


def test_load_rejects_unsupported_mode():
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )

    loader = ResultBlobLoader()
    for mode in ("path", "stream", "json"):
        with pytest.raises(ValueError, match="not supported"):
            loader.load(ref, mode=mode)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SQL filter / tenant binding
# ---------------------------------------------------------------------------


def test_load_filters_by_group_id_when_not_superadmin():
    """SQL fail-closes on cross-tenant: the WHERE clause keyed on
    `id AND group_id` returns nothing for a row owned by another
    group, raising FileNotFoundError downstream."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    captured = {}

    def _fake_fetchone(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return None  # simulate "not found" / cross-tenant

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.load(ref, mode="bytes")

    sql = captured["sql"]
    assert "WHERE id = ? AND group_id = ?" in sql, (
        f"SQL must filter by group_id; got: {sql}"
    )
    assert captured["params"] == ("r1", "g1")


def test_load_skips_group_filter_when_superadmin():
    """is_superadmin=True bypasses the group filter; the bypass is
    recorded in the audit log."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    captured = {}

    def _fake_fetchone(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return {"data": b"payload", "storage_key": None}

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="audit-group",
        result_id="r1",
        is_superadmin=True,
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        out = loader.load(ref, mode="bytes")

    assert "AND group_id" not in captured["sql"]
    assert captured["params"] == ("r1",)
    assert out.data == b"payload"


# ---------------------------------------------------------------------------
# Storage-key dispatch (BYTEA vs object storage)
# ---------------------------------------------------------------------------


def test_load_dispatches_to_object_storage_when_storage_key_present():
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    download_calls = []

    def _fake_fetchone(sql, params=()):
        return {"data": None, "storage_key": "results/p/r.bin"}

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"s3-payload"

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = loader.load(ref, mode="bytes")

    assert download_calls == ["results/p/r.bin"]
    assert out.data == b"s3-payload"
    assert out.size_bytes == len(b"s3-payload")


def test_load_falls_back_to_bytea_when_storage_key_null():
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    download_calls = []

    def _fake_fetchone(sql, params=()):
        return {"data": b"bytea-legacy", "storage_key": None}

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"should-not-fire"

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = loader.load(ref, mode="bytes")

    # No object storage download attempted on the BYTEA path.
    assert download_calls == []
    assert out.data == b"bytea-legacy"


def test_load_converts_memoryview_to_bytes():
    """psycopg2 returns BYTEA as memoryview; the loader normalizes
    to bytes (the legacy contract observable to all callers)."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    raw = b"\x00\x01\x02"
    mv = memoryview(raw)

    def _fake_fetchone(sql, params=()):
        return {"data": mv, "storage_key": None}

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        out = loader.load(ref, mode="bytes")

    assert isinstance(out.data, bytes)
    assert out.data == raw


def test_load_raises_file_not_found_when_row_missing():
    """Row not found -> raise (NOT silent None). The whole point
    of the loader's fail-closed contract."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    def _fake_fetchone(sql, params=()):
        return None

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="missing-id",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.load(ref, mode="bytes")


def test_load_returns_size_bytes_zero_when_object_storage_disabled():
    """Object storage capability off -> data is None but Resource
    is still returned with size_bytes=0. The caller can branch
    on `data is None` (capability off) vs `data == b""` (empty
    blob) vs `data` populated."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    def _fake_fetchone(sql, params=()):
        return {"data": None, "storage_key": "results/p/r.bin"}

    def _fake_download(storage_key):
        return None  # capability disabled / download failed

    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", _fake_fetchone), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = loader.load(ref, mode="bytes")

    assert out.data is None
    assert out.size_bytes == 0


# ---------------------------------------------------------------------------
# Back-compat shim: get_result_data delegates correctly
# ---------------------------------------------------------------------------


def test_back_compat_shim_with_group_id_filters_sql():
    """get_result_data(result_id, group_id) -> dispatch with
    group_id_filter -> SQL filters on (id, group_id)."""
    from app.db import results_db_service

    captured = {}

    def _fake_fetchone(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return {"data": b"x", "storage_key": None}

    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        out = results_db_service.get_result_data("r1", group_id="g1")

    assert out == b"x"
    assert "AND group_id = ?" in captured["sql"]
    assert captured["params"] == ("r1", "g1")


def test_back_compat_shim_without_group_id_skips_filter():
    """get_result_data(result_id) -> dispatch with
    group_id_filter=None -> SQL does NOT filter on group_id
    (legacy fail-open path preserved)."""
    from app.db import results_db_service

    captured = {}

    def _fake_fetchone(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return {"data": b"y", "storage_key": None}

    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        out = results_db_service.get_result_data("r1")

    assert out == b"y"
    assert "AND group_id" not in captured["sql"]
    assert captured["params"] == ("r1",)


def test_back_compat_shim_dispatches_to_object_storage():
    """get_result_data over a row with storage_key set goes through
    the loader's object-storage path (single dispatch)."""
    from app.db import results_db_service

    download_calls = []

    def _fake_fetchone(sql, params=()):
        return {"data": None, "storage_key": "results/p/r.bin"}

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"s3-data"

    with patch("app.db.pg_connection.fetchone", _fake_fetchone), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = results_db_service.get_result_data("r1")

    assert out == b"s3-data"
    assert download_calls == ["results/p/r.bin"]


def test_back_compat_shim_returns_none_when_row_missing():
    """get_result_data(missing) -> None (legacy contract; the
    fail-closed FileNotFoundError lives only on the new
    ResourceRef path)."""
    from app.db import results_db_service

    def _fake_fetchone(sql, params=()):
        return None

    with patch("app.db.pg_connection.fetchone", _fake_fetchone):
        out = results_db_service.get_result_data("missing")

    assert out is None


def test_legacy_s3_download_bytes_still_works():
    """The legacy `_s3_download_bytes(storage_key)` symbol must
    keep returning bytes. It is now a thin wrapper over the
    loader's `_download_object_storage`; the public surface is
    preserved for any external caller still using it."""
    from app.db import results_db_service

    def _fake_download(storage_key):
        return b"ok"

    with patch(
        "app.core.resources.loaders.result_blob._download_object_storage",
        _fake_download,
    ):
        assert results_db_service._s3_download_bytes("results/x/y") == b"ok"
