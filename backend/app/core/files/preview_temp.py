"""
Temporary preview for uploaded files without DB persistence.

Post-Phase4 refactor: RAM-only. Preview e' interamente in memoria —
nessun NamedTemporaryFile, niente extract_zip su disco. Il ZIP e'
navigato via `zipfile.ZipFile(BytesIO(...))` e il parsing usa
`load_by_format_bytes`.
"""

from __future__ import annotations

import logging
import zipfile
from io import BytesIO
from typing import BinaryIO, Optional
from typing_extensions import TypedDict

from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.core.io.zip_guards import (
    pick_zip_member_by_extension,
    validate_zip_bomb_limits,
)


_PREVIEW_INNER_EXTENSIONS = ('.gpkg', '.gdb', '.shp')


_logger_module = logging.getLogger(__name__)


class FilePreviewTempColumnPayload(TypedDict):
    name: str
    type: str


class FilePreviewTempPayload(TypedDict):
    columns: list[FilePreviewTempColumnPayload]
    row_count: int
    format: str
    layers: list[str]
    sheets: list[str]


def preview_file_temp(
    file_content: BinaryIO,
    filename: str,
    *,
    format_detector,
    column_builder,
    logger,
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    # Legacy positional kwargs preserved for backward compat with
    # FileSourceService caller; intentionally ignored.
    fs=None,
    loader=None,
) -> FilePreviewTempPayload:
    """Preview temporanea di un file prima dell'upload definitivo.

    - layer: carica un layer specifico (GPKG/GDB)
    - sheet: carica un foglio specifico (XLSX)
    - Se nessuno specificato su file multi-layer/sheet, restituisce la
      lista disponibile.

    Defense-in-depth: ri-valida block-only sui magic bytes (il router
    gia' lo fa, ma chiamate dirette al service da altre code paths
    non possono assumere quella protezione).

    Args:
        file_content: BinaryIO del file caricato (rewind-able).
        filename: nome file originale (per detect_format + MIME hint).
        format_detector: classe FormatDetector (detect classmethod).
        column_builder: classe ColumnInfoBuilder (from_dataframe staticmethod).
        logger: LoggerProtocol-like (warning/debug usato qui).
        layer: layer GPKG/GDB da caricare (opzionale).
        sheet: foglio XLSX da caricare (opzionale).
        fs, loader: legacy collaborator kwargs, ignorati (RAM-only path
            non li usa). Tenuti per compat callsite finche' non e' tutto
            allineato.

    Returns:
        dict con `columns`, `row_count`, `format`, `layers`, `sheets`.

    Raises:
        ValueError: formato non supportato o MIME mismatch (block-only).
    """
    from app.core.dataframes.load_dispatch import (
        list_excel_sheets_from_bytes,
        load_by_format_bytes,
    )
    from app.core.io.geospatial import list_vector_layers

    file_content.seek(0)
    raw_data = file_content.read()
    try:
        validate_no_executable(content=raw_data, filename=filename)
    except MimeMismatchError as e:
        logger.warning(
            f"preview_file_temp bloccato (MIME): filename={filename!r}: {e}"
        )
        raise ValueError(str(e)) from e

    format_type = format_detector.detect(filename)
    if format_type == 'unknown':
        raise ValueError(f"Formato file non supportato: {filename}")

    inner_bytes = raw_data

    if format_type == 'zip':
        try:
            with zipfile.ZipFile(BytesIO(raw_data), 'r') as z:
                # SAFETY: zip bomb guard prima di `z.read()`.
                validate_zip_bomb_limits(z)

                names = z.namelist()
                inner_name, inner_format, inner_display = pick_zip_member_by_extension(
                    names,
                    extensions=_PREVIEW_INNER_EXTENSIONS,
                    detect_format=format_detector.detect,
                )

                if inner_name is None:
                    raise ValueError(
                        f"ZIP non riconosciuto: nessun file vector "
                        f"supportato. Contenuto: "
                        f"{names[:10]}{'...' if len(names) > 10 else ''}"
                    )

                format_type = inner_format
                if inner_format in ('shp', 'gdb'):
                    # Multi-file: pyogrio /vsizip sullo ZIP intero.
                    inner_bytes = raw_data
                else:
                    inner_bytes = z.read(inner_name)
                    # SAFETY: defense-in-depth MIME su inner file.
                    try:
                        validate_no_executable(
                            content=inner_bytes, filename=inner_display,
                        )
                    except MimeMismatchError as e:
                        logger.warning(
                            f"preview_file_temp bloccato (MIME inner): "
                            f"zip={filename!r} inner={inner_display!r}: {e}"
                        )
                        raise ValueError(str(e)) from e
        except zipfile.BadZipFile as e:
            raise ValueError(f"File ZIP non valido o corrotto: {e}") from e

    # Multi-layer/sheet senza selezione: restituisce lista disponibile.
    if format_type in ['gpkg', 'gdb'] and not layer:
        available_layers = list_vector_layers(inner_bytes)
        if len(available_layers) > 1:
            return {
                "columns": [],
                "row_count": 0,
                "format": format_type,
                "layers": available_layers,
                "sheets": [],
            }
        if available_layers:
            layer = available_layers[0]

    if format_type in ['xlsx', 'xls'] and not sheet:
        available_sheets = list_excel_sheets_from_bytes(inner_bytes)
        if len(available_sheets) > 1:
            return {
                "columns": [],
                "row_count": 0,
                "format": format_type,
                "layers": [],
                "sheets": available_sheets,
            }
        if available_sheets:
            sheet = available_sheets[0]

    is_sheet = format_type in ['xlsx', 'xls']
    df, gdf = load_by_format_bytes(
        inner_bytes,
        format_type,
        layer=None if is_sheet else layer,
        sheet=sheet if is_sheet else None,
    )
    columns = column_builder.from_dataframe(df, gdf)
    row_count = len(df)

    return {
        "columns": [{"name": c.name, "type": c.type} for c in columns],
        "row_count": row_count,
        "format": format_type,
        "layers": [],
        "sheets": [],
    }
