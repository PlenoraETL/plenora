"""
FileSourceBlobLoader contract suite.

BE-GO-READY-RESOURCE-ENGINE-020-FILE-SOURCE-BLOB-LOADER.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.contract]


def _make_ref(
    *,
    file_source_id: str = "fs1",
    group_id: str = "g1",
    is_superadmin: bool = False,
):
    from app.core.resources import ResourceRef

    return ResourceRef(
        kind="file_source_blob",
        expected_group_id=group_id,
        file_source_id=file_source_id,
        is_superadmin=is_superadmin,
    )


def _fake_fetchone(row):
    captured: dict = {}

    def _impl(sql, params=()):
        captured["sql"] = sql
        captured["params"] = params
        return row

    return captured, _impl


# 1. Mode validation


def test_loader_kind_property_is_file_source_blob():
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    assert FileSourceBlobLoader().kind == "file_source_blob"


def test_loader_rejects_mismatched_kind():
    """Calling with a ``ResourceRef`` of the wrong kind must
    raise; the registry routes by kind so this is a defensive
    invariant."""
    from app.core.resources import ResourceRef
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    bad_ref = ResourceRef(
        kind="result_blob",
        expected_group_id="g1",
        result_id="r1",
    )
    with pytest.raises(ValueError, match="does not handle kind"):
        FileSourceBlobLoader().load(bad_ref, mode="bytes")


def test_loader_rejects_path_mode():
    """v1 supports bytes only. ``mode='path'`` is deferred (SHP/GDB
    consumers use ``temp_file_for`` at the service layer)."""
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    ref = _make_ref()
    with pytest.raises(ValueError, match="mode='path' not supported"):
        FileSourceBlobLoader().load(ref, mode="path")


def test_loader_rejects_stream_mode():
    """``mode='stream'`` deferred."""
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    ref = _make_ref()
    with pytest.raises(ValueError, match="mode='stream' not supported"):
        FileSourceBlobLoader().load(ref, mode="stream")


# 2. Tenant binding


def test_default_call_applies_group_id_sql_filter():
    """``is_superadmin=False`` -> the SQL has ``AND group_id = ?``
    AND ``params`` carry the caller's group_id. This is the
    fail-closed surface: a row owned by another group simply does
    not return."""
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    captured, fake = _fake_fetchone({
        "data": b"x", "storage_key": None, "data_size": 1,
    })
    with patch("app.db.pg_connection.fetchone", fake):
        out = FileSourceBlobLoader().load(_make_ref(), mode="bytes")

    assert "AND group_id = ?" in captured["sql"]
    # params: (max_bytes, file_source_id, group_id)
    assert captured["params"][1] == "fs1"
    assert captured["params"][2] == "g1"
    assert out.data == b"x"
    assert out.size_bytes == 1
    assert out.kind == "file_source_blob"


def test_superadmin_bypass_skips_group_filter_and_audits():
    """``is_superadmin=True`` -> SQL has NO ``AND group_id = ?``
    clause AND the audit log records the bypass with the
    canonical ``[AUDIT] FileSourceBlobLoader.load
    is_superadmin=True`` token."""
    from unittest.mock import MagicMock

    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    captured, fake = _fake_fetchone({
        "data": b"x", "storage_key": None, "data_size": 1,
    })

    audit_logger = MagicMock()
    perf_logger = MagicMock()
    # The loader calls `get_logger(__name__)` once for the audit
    # path and again for the perf log; return distinct mocks so
    # we can introspect the audit call without coupling to
    # secondary log lines.
    seen: list = []

    def _fake_get_logger(_name):
        # First call goes to the bypass audit; subsequent ones to
        # the perf log inside _dispatch_by_id / _download_*.
        if not seen:
            seen.append("audit")
            return audit_logger
        return perf_logger

    with patch("app.db.pg_connection.fetchone", fake), patch(
        "app.utils.logger.get_logger", side_effect=_fake_get_logger,
    ):
        FileSourceBlobLoader().load(
            _make_ref(is_superadmin=True), mode="bytes",
        )

    assert "AND group_id = ?" not in captured["sql"]
    audit_logger.info.assert_called_once()
    audit_msg = audit_logger.info.call_args.args[0]
    assert "[AUDIT] FileSourceBlobLoader.load is_superadmin=True" in audit_msg
    assert "file_source_id='fs1'" in audit_msg or "fs1" in audit_msg


def test_missing_row_raises_file_not_found():
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    _captured, fake = _fake_fetchone(None)
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(FileNotFoundError, match="not found"):
            FileSourceBlobLoader().load(_make_ref(), mode="bytes")


# 3. SQL preflight shape


def test_sql_preflight_uses_length_and_case_pattern():
    """The SQL MUST carry the bounded-BYTEA preflight shape so an
    oversized payload never crosses the PG wire. Locks the
    canonical tokens; a regression that drops the CASE would
    re-introduce the unbounded read pattern that R-010.1 closed
    for result_blob."""
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    captured, fake = _fake_fetchone({
        "data": b"x", "storage_key": None, "data_size": 1,
    })
    with patch("app.db.pg_connection.fetchone", fake):
        FileSourceBlobLoader().load(_make_ref(), mode="bytes")

    sql = captured["sql"]
    # Single-line projection so source-bound contract greppers
    # match `length(data) AS data_size` literally.
    assert "length(data) AS data_size" in sql
    # The bounded-BYTEA CASE: oversized data NULLs out at SQL.
    assert "CASE" in sql
    assert "WHEN length(data) > ? THEN NULL" in sql
    assert "ELSE data" in sql
    # WHERE id = ? is the primary key clause.
    assert "FROM file_sources WHERE id = ?" in sql


def test_oversized_bytea_raises_memory_limit_exceeded(monkeypatch):
    """If ``data_size > max_bytes``, the SQL has already nulled
    ``data``; the loader MUST raise ``MemoryLimitExceededError``
    rather than return ``None`` (oversize is a typed signal, not
    a connectivity error)."""
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "1024")  # 1 KiB cap

    # data is None (CASE WHEN length(data) > 1024 THEN NULL).
    # data_size carries the authoritative size from length(data).
    _captured, fake = _fake_fetchone({
        "data": None, "storage_key": None, "data_size": 5_000_000,
    })
    with patch("app.db.pg_connection.fetchone", fake):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            FileSourceBlobLoader().load(_make_ref(), mode="bytes")

    err = exc_info.value
    assert err.observed_bytes == 5_000_000
    assert err.limit_bytes == 1024
    assert err.stage == "file_source_blob_oversize"


# 4. Object-storage branch


class _FakeStorage:
    def __init__(self, chunks):
        self._chunks = chunks

    def get_object_stream(self, _key):
        return {"body": iter(self._chunks)}


def test_storage_key_path_routes_through_bounded_read():
    """When ``storage_key`` is non-null, the loader fetches via
    object storage with bounded chunks. The body iterator is
    consumed by ``read_stream_bounded`` (cap from
    ``RESOURCE_MAX_BYTES``)."""
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    # data=NULL because storage_key is set (CASE branch).
    _captured, fake = _fake_fetchone({
        "data": None,
        "storage_key": "file-sources/abc/data.bin",
        "data_size": None,
    })
    fake_storage = _FakeStorage([b"hello", b"world"])
    with patch("app.db.pg_connection.fetchone", fake), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        out = FileSourceBlobLoader().load(_make_ref(), mode="bytes")

    assert out.data == b"helloworld"
    assert out.size_bytes == 10
    assert out.kind == "file_source_blob"


def test_storage_key_oversize_raises_memory_limit_exceeded(monkeypatch):
    """Object-storage download oversize must surface
    ``MemoryLimitExceededError`` (NOT a fail-soft None) so the
    caller can decide whether to surface a 413 or downgrade."""
    from app.core.resources.exceptions import MemoryLimitExceededError
    from app.core.resources.loaders.file_source_blob import (
        FileSourceBlobLoader,
    )

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "100")  # 100 B cap

    _captured, fake = _fake_fetchone({
        "data": None,
        "storage_key": "file-sources/abc/big.bin",
        "data_size": None,
    })
    # Two 70 B chunks; second push would exceed 100 B.
    fake_storage = _FakeStorage([b"x" * 70, b"y" * 70])
    with patch("app.db.pg_connection.fetchone", fake), patch(
        "app.shared.cloud_backends.get_runtime_object_storage",
        return_value=fake_storage,
    ):
        with pytest.raises(MemoryLimitExceededError) as exc_info:
            FileSourceBlobLoader().load(_make_ref(), mode="bytes")

    err = exc_info.value
    assert err.limit_bytes == 100
    assert err.observed_bytes == 140  # 70 (running) + 70 (next chunk)
    assert err.stage == "file_source_blob_oversize"


# 5. Cap resolution


def test_default_cap_is_200_mib(monkeypatch):
    """Default cap is 200 MiB post BE-GO-READY-MEMORY-ENGINE-005
    (was 500 MiB). BE-GO-READY-MEMORY-ENGINE-007 removed the
    loader's compat alias; tests now call the canonical
    ``resolve_resource_max_bytes`` directly."""
    monkeypatch.delenv("RESOURCE_MAX_BYTES", raising=False)
    from app.core.resources.resource_caps import (
        RESOURCE_MAX_BYTES_DEFAULT,
        resolve_resource_max_bytes,
    )

    assert resolve_resource_max_bytes() == 200 * 1024 * 1024
    assert RESOURCE_MAX_BYTES_DEFAULT == 200 * 1024 * 1024


def test_env_override_is_honored(monkeypatch):
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "12345")
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    assert resolve_resource_max_bytes() == 12345


def test_invalid_env_falls_back_to_default(monkeypatch):
    from app.core.resources.resource_caps import resolve_resource_max_bytes

    monkeypatch.setenv("RESOURCE_MAX_BYTES", "not-an-int")
    assert resolve_resource_max_bytes() == 200 * 1024 * 1024
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "0")
    assert resolve_resource_max_bytes() == 200 * 1024 * 1024
    monkeypatch.setenv("RESOURCE_MAX_BYTES", "-1")
    assert resolve_resource_max_bytes() == 200 * 1024 * 1024


# 6. Registry integration — second loader is registered


def test_default_registry_resolves_file_source_blob_kind():
    """The registry built by ``build_default_registry()`` MUST
    register the new loader. Locks RE-020 graduation."""
    from app.core.resources import build_default_registry

    reg = build_default_registry()
    assert "file_source_blob" in reg._loaders  # noqa: SLF001
    # Both v1 loaders registered side-by-side.
    assert sorted(reg._loaders.keys()) == [  # noqa: SLF001
        "file_source_blob",
        "result_blob",
    ]
