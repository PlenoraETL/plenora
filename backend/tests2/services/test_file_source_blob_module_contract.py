"""Smoke contract for FileSource bytes-compat and streaming read helpers."""

from unittest.mock import MagicMock, patch

import pytest


def test_get_blob_from_db_uses_visibility_and_resource_loader():
    from app.core.files.source_blob import get_blob_from_db

    assert_visible = MagicMock()
    logger = MagicMock()

    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_bytes",
        return_value=b"payload",
    ) as load_blob:
        result = get_blob_from_db(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=assert_visible,
            superadmin_bypass="*",
            logger=logger,
        )

    assert result == b"payload"
    assert_visible.assert_called_once_with("file-1", "grp-1")
    load_blob.assert_called_once_with(
        file_source_id="file-1",
        expected_group_id="grp-1",
        is_superadmin=False,
    )


def test_get_blob_from_db_retries_shared_fetch_with_loader_bypass():
    from app.core.files.source_blob import get_blob_from_db

    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_bytes",
        side_effect=[FileNotFoundError("owned miss"), b"shared-payload"],
    ) as load_blob:
        result = get_blob_from_db(
            "file-1",
            expected_group_id="grp-2",
            assert_visible=MagicMock(),
            superadmin_bypass="*",
            logger=MagicMock(),
        )

    assert result == b"shared-payload"
    assert load_blob.call_args_list[0].kwargs["is_superadmin"] is False
    assert load_blob.call_args_list[1].kwargs["is_superadmin"] is True


def test_open_blob_spool_streams_and_closes_the_seekable_spool():
    from app.core.files.source_blob import open_blob_spool
    from app.core.resources import Resource

    resource = Resource(
        kind="file_source_blob",
        size_bytes=7,
        stream=iter((b"pay", b"load")),
    )
    assert_visible = MagicMock()
    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_stream",
        return_value=resource,
    ) as load_stream:
        with open_blob_spool(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=assert_visible,
            superadmin_bypass="*",
            logger=MagicMock(),
        ) as spool:
            assert spool.read() == b"payload"
            assert not spool.closed

    assert spool.closed
    assert_visible.assert_called_once_with("file-1", "grp-1")
    load_stream.assert_called_once_with(
        file_source_id="file-1",
        expected_group_id="grp-1",
        is_superadmin=False,
    )


def test_get_blob_stream_returns_resource_without_consuming_it():
    from app.core.files.source_blob import get_blob_stream
    from app.core.resources import Resource

    consumed: list[bytes] = []

    def chunks():
        consumed.append(b"payload")
        yield b"payload"

    resource = Resource(
        kind="file_source_blob",
        size_bytes=7,
        stream=chunks(),
    )
    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_stream",
        return_value=resource,
    ):
        out = get_blob_stream(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            superadmin_bypass="*",
            logger=MagicMock(),
        )

    assert out is resource
    assert consumed == []
    assert b"".join(out.stream) == b"payload"


def test_get_blob_stream_does_not_swallow_tenant_denial():
    from app.core.files.source_blob import get_blob_stream

    with pytest.raises(PermissionError, match="denied"):
        get_blob_stream(
            "file-1",
            expected_group_id="grp-2",
            assert_visible=MagicMock(
                side_effect=PermissionError("denied")
            ),
            superadmin_bypass="*",
            logger=MagicMock(),
        )


def test_open_blob_spool_retries_shared_fetch_with_loader_bypass():
    from app.core.files.source_blob import open_blob_spool
    from app.core.resources import Resource

    shared = Resource(
        kind="file_source_blob",
        size_bytes=6,
        stream=iter((b"shared",)),
    )
    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_stream",
        side_effect=[FileNotFoundError("owned miss"), shared],
    ) as load_stream:
        with open_blob_spool(
            "file-1",
            expected_group_id="grp-2",
            assert_visible=MagicMock(),
            superadmin_bypass="*",
            logger=MagicMock(),
        ) as spool:
            assert spool.read() == b"shared"

    assert load_stream.call_args_list[0].kwargs["is_superadmin"] is False
    assert load_stream.call_args_list[1].kwargs["is_superadmin"] is True


def test_open_blob_spool_rolls_over_and_cleans_up():
    from app.core.files.source_blob import open_blob_spool
    from app.core.resources import Resource

    resource = Resource(
        kind="file_source_blob",
        size_bytes=8,
        stream=iter((b"1234", b"5678")),
    )
    with patch(
        "app.core.resources.file_source_blob_access.load_file_source_blob_stream",
        return_value=resource,
    ), patch(
        "app.core.io.bounded_streams.spool_memory_limit",
        return_value=4,
    ):
        with open_blob_spool(
            "file-1",
            expected_group_id="grp-1",
            assert_visible=MagicMock(),
            superadmin_bypass="*",
            logger=MagicMock(),
        ) as spool:
            assert spool.read() == b"12345678"
            assert spool._rolled is True  # noqa: SLF001

    assert spool.closed


def test_temp_file_for_tombstone_removed_phase4():
    """Nessun helper espone path persistenti o cleanup manuale.

    Il data-plane usa invece uno spool anonimo posseduto dal context manager.
    """
    from app.core.files import source_blob

    assert not hasattr(source_blob, "temp_file_for"), (
        "temp_file_for resta presente: il refactor RAM-only non e' completo."
    )
    assert not hasattr(source_blob, "temp_file_for_cm"), (
        "temp_file_for_cm resta presente: il refactor RAM-only non e' completo."
    )


def test_file_source_service_blob_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc._logger = MagicMock()

    with patch("app.core.files.source_blob.get_blob_from_db") as delegated:
        delegated.return_value = b"payload"
        result = svc._get_blob_from_db("file-1", expected_group_id="grp-1")

    assert result == b"payload"
    delegated.assert_called_once_with(
        "file-1",
        expected_group_id="grp-1",
        assert_visible=svc._assert_visible,
        superadmin_bypass=FileSourceService.SUPERADMIN_BYPASS,
        logger=svc._logger,
    )


def test_file_source_service_stream_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc._logger = MagicMock()
    sentinel_cm = MagicMock()

    with patch(
        "app.core.files.source_blob.open_blob_spool",
        return_value=sentinel_cm,
    ) as delegated:
        result = svc._open_blob_spool(
            "file-1", expected_group_id="grp-1",
        )

    assert result is sentinel_cm
    delegated.assert_called_once_with(
        "file-1",
        expected_group_id="grp-1",
        assert_visible=svc._assert_visible,
        superadmin_bypass=FileSourceService.SUPERADMIN_BYPASS,
        logger=svc._logger,
    )


def test_file_source_service_http_stream_facade_delegates():
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._assert_visible = MagicMock()
    svc._logger = MagicMock()
    sentinel_resource = MagicMock()

    with patch(
        "app.core.files.source_blob.get_blob_stream",
        return_value=sentinel_resource,
    ) as delegated:
        result = svc.get_blob_stream(
            "file-1", expected_group_id="grp-1",
        )

    assert result is sentinel_resource
    delegated.assert_called_once_with(
        "file-1",
        expected_group_id="grp-1",
        assert_visible=svc._assert_visible,
        superadmin_bypass=FileSourceService.SUPERADMIN_BYPASS,
        logger=svc._logger,
    )
