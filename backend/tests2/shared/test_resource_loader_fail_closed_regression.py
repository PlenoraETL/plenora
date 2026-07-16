from __future__ import annotations

import pytest

from app.core.resources.loaders.file_source_blob import FileSourceBlobLoader
from app.core.resources.loaders.result_blob import ResultBlobLoader
from app.core.resources.types import ResourceRef


pytestmark = [pytest.mark.unit, pytest.mark.shared, pytest.mark.regression]


def test_result_blob_rejects_visible_row_without_downloaded_payload(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.core.resources.loaders.result_blob._dispatch_by_id",
        lambda *args, **kwargs: (None, "object_storage"),
    )
    ref = ResourceRef(
        kind="result_blob",
        expected_group_id="group-1",
        result_id="result-1",
    )

    with pytest.raises(RuntimeError, match="senza payload"):
        ResultBlobLoader().load(ref, mode="bytes")


@pytest.mark.parametrize("mode", ["bytes", "stream"])
def test_file_source_rejects_visible_row_without_payload_handle(
    monkeypatch,
    mode: str,
) -> None:
    if mode == "bytes":
        monkeypatch.setattr(
            "app.core.resources.loaders.file_source_blob._dispatch_by_id",
            lambda *args, **kwargs: (None, "object_storage"),
        )
    else:
        monkeypatch.setattr(
            "app.core.resources.loaders.file_source_blob._stream_by_id",
            lambda *args, **kwargs: (None, 0, "object_storage"),
        )
    ref = ResourceRef(
        kind="file_source_blob",
        expected_group_id="group-1",
        file_source_id="file-1",
    )

    with pytest.raises(RuntimeError, match="senza (payload|stream)"):
        FileSourceBlobLoader().load(ref, mode=mode)
