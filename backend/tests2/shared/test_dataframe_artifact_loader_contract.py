from unittest.mock import patch

import pandas as pd

from app.core.dataframes.frame_io import load_dataframe_from_artifact


def test_load_dataframe_from_artifact_uses_xlsx_format_hint_without_suffix(tmp_path):
    source = tmp_path / "source.xlsx"
    pd.DataFrame({"id": [1, 2], "name": ["roma", "milano"]}).to_excel(source, index=False)

    extensionless = tmp_path / "plenora-result-blob-no-suffix"
    extensionless.write_bytes(source.read_bytes())

    df = load_dataframe_from_artifact(path=extensionless, format_hint="xlsx")

    assert df.to_dict("records") == [
        {"id": 1, "name": "roma"},
        {"id": 2, "name": "milano"},
    ]


def test_load_dataframe_from_artifact_can_read_tabular_bytes_without_tempfile():
    data = b"id,name\n1,roma\n2,milano\n"

    df = load_dataframe_from_artifact(data=data, format_hint="csv")

    assert df.to_dict("records") == [
        {"id": 1, "name": "roma"},
        {"id": 2, "name": "milano"},
    ]


def test_load_dataframe_from_artifact_keeps_path_bound_formats_on_path(tmp_path):
    extensionless = tmp_path / "plenora-result-blob-no-suffix"
    extensionless.write_bytes(b"fake geopackage payload")
    expected = pd.DataFrame({"ok": [1]})

    with patch("app.core.dataframes.frame_io.load_dataframe_from_file", return_value=expected) as load_file:
        df = load_dataframe_from_artifact(path=extensionless, format_hint="geopackage")

    assert df is expected
    delegated_path = load_file.call_args.args[0]
    assert delegated_path.suffix == ".gpkg"
