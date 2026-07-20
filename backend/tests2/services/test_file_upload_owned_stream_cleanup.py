from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.core.files import upload


pytestmark = [pytest.mark.unit, pytest.mark.contract]


class _TrackingStream(io.BytesIO):
    was_closed = False

    def close(self):
        self.was_closed = True
        super().close()


def _zip_payload() -> io.BytesIO:
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("data.csv", b"id\n1\n")
    outer.seek(0)
    return outer


@pytest.mark.parametrize("failure", ["dispatch", "db"])
def test_zip_inner_spool_is_closed_on_post_parse_failure(monkeypatch, failure):
    owned = _TrackingStream(b"id\n1\n")
    monkeypatch.setattr(upload, "spool_zip_member", lambda *_a, **_k: owned)
    monkeypatch.setattr(
        upload.load_dispatch_module,
        "load_by_format_stream",
        lambda *_a, **_k: (pd.DataFrame({"id": [1]}), None),
    )

    dispatch = MagicMock(return_value={"storage_key": "k", "size_bytes": 5})
    db_save = MagicMock()
    if failure == "dispatch":
        dispatch.side_effect = RuntimeError("upload failed")
    else:
        db_save.side_effect = RuntimeError("db failed")

    with pytest.raises(RuntimeError):
        upload.create_from_upload(
            _zip_payload(),
            "bundle.zip",
            "dataset",
            generate_file_id=lambda _name: "file-1",
            time_now=lambda: datetime.now(timezone.utc),
            detect_format=lambda name: "zip" if name.endswith(".zip") else "csv",
            column_builder_from_df=lambda *_a: [],
            dispatch_blob_storage=dispatch,
            db_save_file_source=db_save,
            build_file_source=lambda **kw: SimpleNamespace(**kw),
            default_group="default",
            logger=MagicMock(),
        )

    assert owned.was_closed is True
