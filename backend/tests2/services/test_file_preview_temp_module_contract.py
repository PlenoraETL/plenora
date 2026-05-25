"""Smoke contract: preview_file_temp (RAM-only, Phase4)."""

from __future__ import annotations

import io
import zipfile as _zipfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


pytestmark = [pytest.mark.unit, pytest.mark.contract]


@pytest.fixture
def fake_logger():
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.debug = MagicMock()
    log.error = MagicMock()
    return log


@pytest.fixture
def real_helpers():
    """Restituisce i veri FormatDetector + ColumnInfoBuilder (sono pure)."""
    from app.core.files.helpers import ColumnInfoBuilder, FormatDetector
    return FormatDetector, ColumnInfoBuilder


# 1. Happy path: CSV minimo


def test_preview_csv_returns_columns_and_row_count(fake_logger, real_helpers):
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    content = io.BytesIO(b"id,name\n1,a\n2,b\n3,c\n")
    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(
            pd.DataFrame({"id": [1, 2, 3], "name": ["a", "b", "c"]}),
            None,
        ),
    ):
        out = preview_file_temp(
            content, "data.csv",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    assert out["format"] == "csv"
    assert out["row_count"] == 3
    by_name = {c["name"]: c["type"] for c in out["columns"]}
    assert by_name == {"id": "int", "name": "str"}
    assert out["layers"] == []
    assert out["sheets"] == []


# 2. MIME validation defense-in-depth


def test_preview_blocks_executable_mime_with_value_error(
    fake_logger, real_helpers,
):
    """Payload PE (Windows .exe magic 'MZ') -> bloccato dal validator."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    pe_bytes = b"MZ\x90\x00" + b"\x00" * 256
    content = io.BytesIO(pe_bytes)

    with pytest.raises(ValueError):
        preview_file_temp(
            content, "innocent_looking.csv",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    fake_logger.warning.assert_called_once()
    msg = fake_logger.warning.call_args[0][0]
    assert "preview_file_temp bloccato (MIME)" in msg


# 3. Format unknown


def test_preview_unknown_format_raises_value_error(fake_logger, real_helpers):
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    content = io.BytesIO(b"some random bytes that aren't any known format")
    with pytest.raises(ValueError, match="Formato file non supportato"):
        preview_file_temp(
            content, "weird.xyz",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )


# 4. RAM-only: nessun tmp file su disco (by-construction)


def test_preview_no_filesystem_io_in_csv_happy_path(
    fake_logger, real_helpers, tmp_path, monkeypatch,
):
    """Anti-regression Phase4: preview RAM-only NON crea nessun tmp file.
    Pre-fix usava NamedTemporaryFile + mkdtemp; post-fix lavora solo bytes."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    fake_settings = MagicMock()
    fake_settings.TEMP_DIR = str(tmp_path)
    monkeypatch.setattr("app.config.settings", fake_settings, raising=False)

    pre = set(tmp_path.iterdir())
    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"a": [1]}), None),
    ):
        preview_file_temp(
            io.BytesIO(b"a\n1\n"), "x.csv",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )
    post = set(tmp_path.iterdir())
    assert post == pre, f"tmp file leak su success: {post - pre}"


# 5. FileSourceService.preview_file_temp resta un facade core sottile


def test_file_source_service_preview_delegates_with_service_collaborators():
    """`FileSourceService.preview_file_temp` delega al modulo core con
    helper classi (FormatDetector, ColumnInfoBuilder)."""
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._fs = MagicMock()
    svc._loader = MagicMock()
    from app.core.files.helpers import ColumnInfoBuilder, FormatDetector
    svc._format_detector = FormatDetector
    svc._column_builder = ColumnInfoBuilder
    svc._logger = MagicMock()

    with patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
        return_value=(pd.DataFrame({"k": [1, 2]}), None),
    ):
        out = svc.preview_file_temp(io.BytesIO(b"k\n1\n2\n"), "tiny.csv")
    assert out["format"] == "csv"
    assert out["row_count"] == 2
    assert {c["name"] for c in out["columns"]} == {"k"}


# 6. GPKG multi-layer senza layer -> early return con lista layers


def test_preview_gpkg_multi_layer_without_selection_returns_layers(
    fake_logger, real_helpers,
):
    """Multi-layer GPKG senza `layer`: ritorna `layers` list."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    content = io.BytesIO(b"non-executable bytes for gpkg placeholder")
    with patch(
        "app.core.io.geospatial.list_vector_layers",
        return_value=["roads", "buildings"],
    ) as list_vector, patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
    ) as load_lbf:
        out = preview_file_temp(
            content, "data.gpkg",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    assert out["format"] == "gpkg"
    assert out["columns"] == []
    assert out["row_count"] == 0
    assert out["layers"] == ["roads", "buildings"]
    assert out["sheets"] == []
    list_vector.assert_called_once()
    load_lbf.assert_not_called()


# 7. XLSX multi-sheet senza sheet -> early return con lista sheets


def test_preview_xlsx_multi_sheet_without_selection_returns_sheets(
    fake_logger, real_helpers,
):
    """Multi-sheet XLSX senza `sheet`: ritorna `sheets` list."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    content = io.BytesIO(b"non-executable bytes for xlsx placeholder")
    with patch(
        "app.core.dataframes.load_dispatch.list_excel_sheets_from_bytes",
        return_value=["Sheet1", "Sheet2"],
    ) as list_sheets, patch(
        "app.core.dataframes.load_dispatch.load_by_format_bytes",
    ) as load_lbf:
        out = preview_file_temp(
            content, "report.xlsx",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    assert out["format"] == "xlsx"
    assert out["columns"] == []
    assert out["row_count"] == 0
    assert out["sheets"] == ["Sheet1", "Sheet2"]
    assert out["layers"] == []
    list_sheets.assert_called_once()
    load_lbf.assert_not_called()


# 8. ZIP unwrap -> dispatch al format interno (gpkg)


def test_preview_zip_unwrap_dispatches_to_inner_gpkg_format(
    fake_logger, real_helpers,
):
    """ZIP RAM-only che wrappa un .gpkg: navigato via zipfile.ZipFile(BytesIO),
    format passa da 'zip' a 'gpkg', list_vector_layers chiamato su inner bytes."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    buf = io.BytesIO()
    with _zipfile.ZipFile(buf, "w") as z:
        z.writestr("inner.gpkg", b"fake gpkg payload")
    zip_payload = buf.getvalue()

    with patch(
        "app.core.io.geospatial.list_vector_layers",
        return_value=["a", "b"],
    ) as list_vector:
        out = preview_file_temp(
            io.BytesIO(zip_payload), "bundle.zip",
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    list_vector.assert_called_once()
    # Format dispatch riuscito: dopo l'unwrap risulta gpkg.
    assert out["format"] == "gpkg"
    assert out["layers"] == ["a", "b"]
    assert out["sheets"] == []
