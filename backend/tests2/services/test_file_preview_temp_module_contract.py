"""
Smoke contract: estrazione preview_file_temp.

Verifica le invarianti minime del refactor:
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

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
def fake_fs():
    """Mock di FileSystemProtocol (extract_zip/iterdir/is_dir non usati nei test CSV)."""
    fs = MagicMock()
    fs.extract_zip = MagicMock()
    fs.iterdir = MagicMock(return_value=[])
    fs.is_dir = MagicMock(return_value=False)
    return fs


@pytest.fixture
def real_helpers():
    """Restituisce i veri FormatDetector + ColumnInfoBuilder (sono pure)."""
    from app.core.files.helpers import ColumnInfoBuilder, FormatDetector
    return FormatDetector, ColumnInfoBuilder


@pytest.fixture
def patched_settings_temp_dir(tmp_path, monkeypatch):
    """Patcha settings.TEMP_DIR a tmp_path per isolare i temp file dei test."""
    fake_settings = MagicMock()
    fake_settings.TEMP_DIR = str(tmp_path)
    monkeypatch.setattr("app.config.settings", fake_settings)
    return tmp_path


# 1. Happy path: CSV minimo


def test_preview_csv_returns_columns_and_row_count(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()
    loader.load_csv = MagicMock(return_value=pd.DataFrame({
        "id": [1, 2, 3], "name": ["a", "b", "c"],
    }))

    content = io.BytesIO(b"id,name\n1,a\n2,b\n3,c\n")
    out = preview_file_temp(
        content, "data.csv",
        fs=fake_fs, loader=loader,
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
    loader.load_csv.assert_called_once()


# 2. MIME validation defense-in-depth


def test_preview_blocks_executable_mime_with_value_error(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    """Payload PE (Windows .exe magic 'MZ') -> bloccato dal validator."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()

    # Magic bytes PE Windows: "MZ" all'inizio
    pe_bytes = b"MZ\x90\x00" + b"\x00" * 256
    content = io.BytesIO(pe_bytes)

    with pytest.raises(ValueError):
        preview_file_temp(
            content, "innocent_looking.csv",
            fs=fake_fs, loader=loader,
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )

    fake_logger.warning.assert_called_once()
    msg = fake_logger.warning.call_args[0][0]
    assert "preview_file_temp bloccato (MIME)" in msg
    loader.load_csv.assert_not_called()


# 3. Format unknown


def test_preview_unknown_format_raises_value_error(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()

    content = io.BytesIO(b"some random bytes that aren't any known format")
    with pytest.raises(ValueError, match="Formato file non supportato"):
        preview_file_temp(
            content, "weird.xyz",
            fs=fake_fs, loader=loader,
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )


# 4. Cleanup garantito su success e exception


def test_preview_cleans_up_tmp_file_on_success(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    """Dopo una preview success, la tmp dir non deve contenere file residui."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()
    loader.load_csv = MagicMock(return_value=pd.DataFrame({"a": [1]}))

    pre = set(patched_settings_temp_dir.iterdir())
    preview_file_temp(
        io.BytesIO(b"a\n1\n"), "x.csv",
        fs=fake_fs, loader=loader,
        format_detector=FormatDetector,
        column_builder=ColumnInfoBuilder,
        logger=fake_logger,
    )
    post = set(patched_settings_temp_dir.iterdir())
    assert post == pre, f"tmp file leak su success: {post - pre}"


def test_preview_cleans_up_tmp_file_on_parser_exception(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    """Se il parser solleva, il tmp file deve comunque essere ripulito (finally)."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()
    loader.load_csv = MagicMock(side_effect=RuntimeError("parser exploded"))

    pre = set(patched_settings_temp_dir.iterdir())
    with pytest.raises(RuntimeError, match="parser exploded"):
        preview_file_temp(
            io.BytesIO(b"a\n1\n"), "x.csv",
            fs=fake_fs, loader=loader,
            format_detector=FormatDetector,
            column_builder=ColumnInfoBuilder,
            logger=fake_logger,
        )
    post = set(patched_settings_temp_dir.iterdir())
    assert post == pre, f"tmp file leak su exception: {post - pre}"


# 5. FileSourceService.preview_file_temp resta un facade core sottile


def test_file_source_service_preview_delegates_with_service_collaborators(
    patched_settings_temp_dir,
):
    """`FileSourceService.preview_file_temp` delega al modulo core:
    passa self._fs/self._loader/self._format_detector/self._column_builder/
    self._logger alla funzione modulo."""
    from app.core.files.service import FileSourceService

    svc = FileSourceService.__new__(FileSourceService)
    svc._fs = MagicMock()
    svc._loader = MagicMock()
    svc._loader.load_csv = MagicMock(return_value=pd.DataFrame({"k": [1, 2]}))
    from app.core.files.helpers import ColumnInfoBuilder, FormatDetector
    svc._format_detector = FormatDetector
    svc._column_builder = ColumnInfoBuilder
    svc._logger = MagicMock()

    out = svc.preview_file_temp(io.BytesIO(b"k\n1\n2\n"), "tiny.csv")
    assert out["format"] == "csv"
    assert out["row_count"] == 2
    assert {c["name"] for c in out["columns"]} == {"k"}


# 6. parser dispatch (load-dispatch boundary: load_by_format e' ora condivisa)


def test_load_by_format_unsupported_raises(fake_logger, tmp_path):
    """load_by_format con format non riconosciuto -> ValueError esplicito.

    Post load-dispatch boundary: la funzione vive in `app.core.files.load_dispatch`
    (modulo condiviso tra FileSourceService e file_preview_temp).
    """
    from app.core.files.load_dispatch import load_by_format

    loader = MagicMock()
    with pytest.raises(ValueError, match="Formato 'foo' non supportato"):
        load_by_format(loader, tmp_path / "x", "foo", None, None, logger=fake_logger)


# 7. GPKG multi-layer senza layer -> early return con lista layers


def test_preview_gpkg_multi_layer_without_selection_returns_layers(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    """Multi-layer GPKG senza `layer`: ritorna `layers` list, NON carica
    geospatial (early return prima di _load_by_format)."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()
    loader.list_layers = MagicMock(return_value=["roads", "buildings"])
    loader.load_geospatial = MagicMock()

    content = io.BytesIO(b"non-executable bytes for gpkg placeholder")
    out = preview_file_temp(
        content, "data.gpkg",
        fs=fake_fs, loader=loader,
        format_detector=FormatDetector,
        column_builder=ColumnInfoBuilder,
        logger=fake_logger,
    )

    assert out["format"] == "gpkg"
    assert out["columns"] == []
    assert out["row_count"] == 0
    assert out["layers"] == ["roads", "buildings"]
    assert out["sheets"] == []
    loader.list_layers.assert_called_once()
    loader.load_geospatial.assert_not_called()


# 8. XLSX multi-sheet senza sheet -> early return con lista sheets


def test_preview_xlsx_multi_sheet_without_selection_returns_sheets(
    fake_logger, fake_fs, real_helpers, patched_settings_temp_dir,
):
    """Multi-sheet XLSX senza `sheet`: ritorna `sheets` list, NON carica
    excel (early return prima di _load_by_format)."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers
    loader = MagicMock()
    loader.list_sheets = MagicMock(return_value=["Sheet1", "Sheet2"])
    loader.load_excel = MagicMock()

    content = io.BytesIO(b"non-executable bytes for xlsx placeholder")
    out = preview_file_temp(
        content, "report.xlsx",
        fs=fake_fs, loader=loader,
        format_detector=FormatDetector,
        column_builder=ColumnInfoBuilder,
        logger=fake_logger,
    )

    assert out["format"] == "xlsx"
    assert out["columns"] == []
    assert out["row_count"] == 0
    assert out["sheets"] == ["Sheet1", "Sheet2"]
    assert out["layers"] == []
    loader.list_sheets.assert_called_once()
    loader.load_excel.assert_not_called()


# 9. ZIP unwrap -> dispatch al format interno (gpkg)


def test_preview_zip_unwrap_dispatches_to_inner_gpkg_format(
    fake_logger, real_helpers, patched_settings_temp_dir,
):
    """ZIP che wrappa un .gpkg: extract_zip + iterdir riconosce l'estensione,
    format passa da 'zip' a 'gpkg', list_layers e' chiamato sul path inner.
    Esercitato il branch ZIP unwrap senza dover wrappare uno zip reale."""
    from app.core.files.preview_temp import preview_file_temp

    FormatDetector, ColumnInfoBuilder = real_helpers

    # Path inner reale (esistente) per soddisfare il cleanup _rmtree_safe finale.
    inner = patched_settings_temp_dir / "inner.gpkg"
    inner.touch()

    fake_fs = MagicMock()
    fake_fs.extract_zip = MagicMock()
    fake_fs.iterdir = MagicMock(return_value=[inner])
    # is_dir(inner) -> False (e' un file .gpkg, non una dir .gdb)
    fake_fs.is_dir = MagicMock(return_value=False)

    loader = MagicMock()
    # Multi-layer: early return, no parser call.
    loader.list_layers = MagicMock(return_value=["a", "b"])
    loader.load_geospatial = MagicMock()

    content = io.BytesIO(b"PK\x03\x04 fake zip header bytes")
    out = preview_file_temp(
        content, "bundle.zip",
        fs=fake_fs, loader=loader,
        format_detector=FormatDetector,
        column_builder=ColumnInfoBuilder,
        logger=fake_logger,
    )

    fake_fs.extract_zip.assert_called_once()
    fake_fs.iterdir.assert_called_once()
    # list_layers chiamato sul path INNER (post-unwrap), non sul .zip originale.
    loader.list_layers.assert_called_once_with(inner)
    # Format dispatch riuscito: dopo l'unwrap risulta gpkg.
    assert out["format"] == "gpkg"
    assert out["layers"] == ["a", "b"]
    assert out["sheets"] == []
    assert out["columns"] == []
    assert out["row_count"] == 0
    loader.load_geospatial.assert_not_called()
