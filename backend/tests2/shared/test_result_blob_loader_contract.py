"""
Contract tests for `ResultBlobLoader`.

BE-GO-READY-RESOURCE-ENGINE-002-RESULT-BLOB-LOADER-PILOT.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

# BE-GO-READY-TEST-INFRA-003: shared helpers extracted to a sibling
# module. ResourceRef construction, fake_fetchone/dispatch capture
# patterns, and Resource path cleanup are now deduplicated across
# the four resource-loader contract suites without any change to
# assertion semantics.
from tests2.shared._resource_loader_helpers import (
    fake_fetchone,
    make_result_ref,
)


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


# Fail-closed input validation


def test_load_rejects_wrong_kind():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    # Construct a ref that's syntactically valid for ResourceRef
    # but whose `kind` is mismatched against the loader. We bypass
    # the Literal type check since the test is about runtime
    # enforcement at the loader, not at type construction.
    ref = make_result_ref()
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
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = make_result_ref()
    object.__setattr__(ref, "result_id", None)

    loader = ResultBlobLoader()
    with pytest.raises(ValueError, match="result_id required"):
        loader.load(ref, mode="bytes")


def test_load_rejects_unsupported_mode():
    """v1 + 004 supports mode='bytes' AND mode='path'. Other modes
    (notably 'stream', plus invalid strings like 'json') still
    fail-closed at construction time. The mode='path' support
    landed in BE-GO-READY-RESOURCE-ENGINE-004 and is locked by
    the dedicated path-mode contract test file."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    ref = make_result_ref()

    loader = ResultBlobLoader()
    for mode in ("stream", "json"):
        with pytest.raises(ValueError, match="not supported"):
            loader.load(ref, mode=mode)  # type: ignore[arg-type]


# SQL filter / tenant binding


def test_load_filters_by_group_id_when_not_superadmin():
    """SQL fail-closes on cross-tenant: the WHERE clause keyed on
    `id AND group_id` returns nothing for a row owned by another
    group, raising FileNotFoundError downstream.

    013 note: the WHERE shape is unchanged; the leading SQL
    parameter is the BYTEA preflight cap (max_bytes), followed
    by the WHERE-clause params in declaration order.
    """
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake = fake_fetchone(row=None)  # simulate "not found" / cross-tenant

    ref = make_result_ref()
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.load(ref, mode="bytes")

    sql = captured["sql"]
    assert "WHERE id = %s AND group_id = %s" in sql, (
        f"SQL must filter by group_id; got: {sql}"
    )
    # 013: max_bytes is the leading bound parameter (CASE cap).
    expected_cap = resolve_resource_max_bytes()
    assert captured["params"] == (expected_cap, "r1", "g1")


def test_load_skips_group_filter_when_superadmin():
    """is_superadmin=True bypasses the group filter; the bypass is
    recorded in the audit log."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake = fake_fetchone(
        row={"data": b"payload", "data_size": 7, "storage_key": None}
    )

    ref = make_result_ref(group_id="audit-group", is_superadmin=True)
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake):
        out = loader.load(ref, mode="bytes")

    assert "AND group_id" not in captured["sql"]
    # 013: max_bytes precedes the WHERE-clause params; with no
    # group filter the only WHERE-bound value is `r1`.
    expected_cap = resolve_resource_max_bytes()
    assert captured["params"] == (expected_cap, "r1")
    assert out.data == b"payload"


# Storage-key dispatch (BYTEA vs object storage)


def test_load_dispatches_to_object_storage_when_storage_key_present():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    download_calls = []
    _, fake_fetch = fake_fetchone(
        row={"data": None, "storage_key": "results/p/r.bin"}
    )

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"s3-payload"

    ref = make_result_ref()
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake_fetch), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = loader.load(ref, mode="bytes")

    assert download_calls == ["results/p/r.bin"]
    assert out.data == b"s3-payload"
    assert out.size_bytes == len(b"s3-payload")


def test_fetch_row_returns_typed_lookup_row():
    from app.core.resources.loaders.result_blob import (
        ResultBlobLookupRow,
        _fetch_row,
    )
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake_fetch = fake_fetchone(
        row={
            "project_id": "proj-1",
            "data": b"bytea",
            "data_size": 5,
            "storage_key": None,
        }
    )

    with patch("app.db.pg_connection.fetchone", fake_fetch):
        row = _fetch_row(
            "r1",
            max_bytes=resolve_resource_max_bytes(),
            group_id_filter="g1",
            project_id_filter="proj-1",
        )

    assert isinstance(row, ResultBlobLookupRow)
    assert row.project_id == "proj-1"
    assert row.data == b"bytea"
    assert row.data_size == 5
    assert row.storage_key is None
    assert "AND project_id = %s" in captured["sql"]


def test_load_falls_back_to_bytea_when_storage_key_null():
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    download_calls = []
    _, fake_fetch = fake_fetchone(
        row={"data": b"bytea-legacy", "storage_key": None}
    )

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"should-not-fire"

    ref = make_result_ref()
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake_fetch), \
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
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    raw = b"\x00\x01\x02"
    mv = memoryview(raw)
    _, fake = fake_fetchone(row={"data": mv, "storage_key": None})

    ref = make_result_ref()
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake):
        out = loader.load(ref, mode="bytes")

    assert isinstance(out.data, bytes)
    assert out.data == raw


def test_load_raises_file_not_found_when_row_missing():
    """Row not found -> raise (NOT silent None). The whole point
    of the loader's fail-closed contract."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    _, fake = fake_fetchone(row=None)

    ref = make_result_ref(result_id="missing-id")
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(FileNotFoundError, match="not found"):
            loader.load(ref, mode="bytes")


def test_load_returns_size_bytes_zero_when_object_storage_disabled():
    """Object storage capability off -> data is None but Resource
    is still returned with size_bytes=0. The caller can branch
    on `data is None` (capability off) vs `data == b""` (empty
    blob) vs `data` populated."""
    from app.core.resources.loaders.result_blob import ResultBlobLoader

    _, fake_fetch = fake_fetchone(
        row={"data": None, "storage_key": "results/p/r.bin"}
    )

    def _fake_download(storage_key):
        return None  # capability disabled / download failed

    ref = make_result_ref()
    loader = ResultBlobLoader()
    with patch("app.db.pg_connection.fetchone", fake_fetch), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = loader.load(ref, mode="bytes")

    assert out.data is None
    assert out.size_bytes == 0


# Result blob read boundary delegation


def test_results_blob_boundary_with_group_id_filters_sql():
    """Canonical get_result_data(result_id, group_id) delegates to the
    Resource Engine dispatch with the same tenant filter semantics."""
    from app.core.results import blob as results_blob
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake = fake_fetchone(
        row={"data": b"x", "data_size": 1, "storage_key": None}
    )

    with patch("app.db.pg_connection.fetchone", fake):
        out = results_blob.get_result_data("r1", group_id="g1")

    assert out == b"x"
    assert "AND group_id = %s" in captured["sql"]
    expected_cap = resolve_resource_max_bytes()
    assert captured["params"] == (expected_cap, "r1", "g1")


def test_result_blob_boundary_with_group_id_filters_sql_against_core_path():
    """get_result_data(result_id, group_id) -> dispatch with
    group_id_filter -> SQL filters on (id, group_id).

    013 note: max_bytes leads the params tuple (CASE preflight
    placeholder); WHERE-clause params follow.
    """
    from app.core.results import blob as results_blob
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake = fake_fetchone(
        row={"data": b"x", "data_size": 1, "storage_key": None}
    )

    with patch("app.db.pg_connection.fetchone", fake):
        out = results_blob.get_result_data("r1", group_id="g1")

    assert out == b"x"
    assert "AND group_id = %s" in captured["sql"]
    expected_cap = resolve_resource_max_bytes()
    assert captured["params"] == (expected_cap, "r1", "g1")


def test_result_blob_boundary_without_group_id_skips_filter():
    """get_result_data(result_id) -> dispatch with
    group_id_filter=None -> SQL does NOT filter on group_id
    (legacy fail-open path preserved)."""
    from app.core.results import blob as results_blob
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    captured, fake = fake_fetchone(
        row={"data": b"y", "data_size": 1, "storage_key": None}
    )

    with patch("app.db.pg_connection.fetchone", fake):
        out = results_blob.get_result_data("r1")

    assert out == b"y"
    assert "AND group_id" not in captured["sql"]
    expected_cap = resolve_resource_max_bytes()
    assert captured["params"] == (expected_cap, "r1")


def test_result_blob_boundary_dispatches_to_object_storage():
    """get_result_data over a row with storage_key set goes through
    the loader's object-storage path (single dispatch)."""
    from app.core.results import blob as results_blob

    download_calls = []
    _, fake_fetch = fake_fetchone(
        row={"data": None, "storage_key": "results/p/r.bin"}
    )

    def _fake_download(storage_key):
        download_calls.append(storage_key)
        return b"s3-data"

    with patch("app.db.pg_connection.fetchone", fake_fetch), \
         patch(
             "app.core.resources.loaders.result_blob._download_object_storage",
             _fake_download,
         ):
        out = results_blob.get_result_data("r1")

    assert out == b"s3-data"
    assert download_calls == ["results/p/r.bin"]


def test_result_blob_boundary_returns_none_when_row_missing():
    """get_result_data(missing) -> None (legacy contract; the
    fail-closed FileNotFoundError lives only on the new
    ResourceRef path)."""
    from app.core.results import blob as results_blob

    _, fake = fake_fetchone(row=None)

    with patch("app.db.pg_connection.fetchone", fake):
        out = results_blob.get_result_data("missing")

    assert out is None


def test_result_blob_loader_object_storage_download_still_works():
    """The canonical object-storage loader returns bytes."""
    from app.core.resources.loaders import result_blob

    def _fake_download(storage_key, **_):
        return b"ok"

    with patch(
        "app.core.resources.loaders.result_blob._download_object_storage_impl",
        _fake_download,
    ):
        assert result_blob._download_object_storage("results/x/y") == b"ok"
