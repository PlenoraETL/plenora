from __future__ import annotations

from types import SimpleNamespace

import pandas as pd


def test_datasource_legacy_xls_is_written_and_named_as_xlsx(monkeypatch):
    from app.core.carica import dispatch

    captured: dict[str, object] = {}

    class _DataSourceService:
        def write_chunks(self, datasource_id, path, chunks):
            captured["datasource_id"] = datasource_id
            captured["path"] = path
            captured["payload"] = b"".join(chunks)

    monkeypatch.setattr(
        dispatch,
        "_get_project_service",
        lambda: SimpleNamespace(
            get_project_metadata=lambda _project_id: SimpleNamespace(group_id="g1")
        ),
    )
    monkeypatch.setattr(dispatch, "is_resource_visible", lambda *_a, **_k: True)
    monkeypatch.setattr(dispatch, "_get_datasource_service", _DataSourceService)

    outcome = dispatch.execute_datasource_write(
        pd.DataFrame({"id": [1]}),
        {
            "datasource_id": "ds1",
            "dest_folder": "exports",
            "dest_filename": "report.xls",
            "dest_format": "xls",
        },
        "p1",
        "t1",
        progress=lambda *_args: None,
    )

    assert outcome["success"] is True
    assert captured["path"] == "exports/report.xlsx"
    assert captured["payload"].startswith(b"PK")
