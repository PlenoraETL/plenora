"""Smoke contract for file source blob/materialization helpers."""

from pathlib import Path
from unittest.mock import MagicMock, patch


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


def test_temp_file_for_materializes_single_file_with_original_suffix(tmp_path):
    from app.core.files.source_blob import temp_file_for

    result = temp_file_for(
        "file-1",
        expected_group_id="grp-1",
        db_get_file_source=MagicMock(
            return_value={"format": "csv", "original_filename": "source.csv"}
        ),
        get_blob_from_db=MagicMock(return_value=b"a,b\n1,2\n"),
        temp_dir=str(tmp_path),
        mkdtemp_writable=MagicMock(),
        safe_extractall=MagicMock(),
        rmtree_safe=MagicMock(),
        logger=MagicMock(),
    )

    assert isinstance(result, Path)
    assert result.suffix == ".csv"
    assert result.read_bytes() == b"a,b\n1,2\n"
    result.unlink()


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
