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
from typing import BinaryIO, Optional
from typing_extensions import TypedDict

from app.core.dataframes import load_dispatch as load_dispatch_module
from app.core.dataframes import preview_loader as preview_loader_module
from app.core.io import geospatial as geospatial_io_module
from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.core.io.zip_guards import (
    open_zip_bytes_safe,
    pick_zip_member_by_extension,
)


_PREVIEW_INNER_EXTENSIONS = ('.gpkg', '.gdb', '.shp')


_logger_module = logging.getLogger(__name__)


class FilePreviewTempColumnPayload(TypedDict):
    name: str
    type: str


class FilePreviewTempPayload(TypedDict):
    columns: list[FilePreviewTempColumnPayload]
    row_count: int
    # H-4 (2026-05-26): True quando row_count e' un lower-bound del totale
    # (bounded preview cap, no metadata cheap dal formato). Il FE deve
    # mostrare disclaimer "stima" all'utente. Casi True: csv/jsonlines/xlsx
    # (require full scan); False: parquet/geospatial (totale da metadata).
    row_count_is_estimate: bool
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
    Returns:
        dict con `columns`, `row_count`, `format`, `layers`, `sheets`.
        Post-RAM-1: `row_count` puo' essere `None` quando il formato
        non espone metadata cheap (csv, jsonlines, xlsx); il frontend
        deve interpretare `None` come "row count non disponibile in
        preview, riprovare dopo upload definitivo".

    Raises:
        ValueError: formato non supportato o MIME mismatch (block-only).
    """
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
            # `open_zip_bytes_safe` valida i cap zip-bomb PRIMA di yield.
            with open_zip_bytes_safe(raw_data) as z:
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
        available_layers = geospatial_io_module.list_vector_layers(inner_bytes)
        if len(available_layers) > 1:
            return {
                "columns": [],
                "row_count": 0,
                "row_count_is_estimate": False,
                "format": format_type,
                "layers": available_layers,
                "sheets": [],
            }
        if available_layers:
            layer = available_layers[0]

    if format_type in ['xlsx', 'xls'] and not sheet:
        available_sheets = load_dispatch_module.list_excel_sheets_from_bytes(
            inner_bytes
        )
        if len(available_sheets) > 1:
            return {
                "columns": [],
                "row_count": 0,
                "row_count_is_estimate": False,
                "format": format_type,
                "layers": [],
                "sheets": available_sheets,
            }
        if available_sheets:
            sheet = available_sheets[0]

    is_sheet = format_type in ['xlsx', 'xls']
    # RAM-1 (2026-05-26): bounded preview — carica al massimo `max_rows`
    # righe/feature invece del DataFrame intero. Per CSV/XLSX/JSON-lines
    # il row_count totale non e' disponibile cheap (None); per parquet
    # e geospatial viene da metadata.
    df_preview, total_row_count = preview_loader_module.preview_dataframe_from_bytes(
        inner_bytes,
        format_type,
        layer=None if is_sheet else layer,
        sheet=sheet if is_sheet else None,
    )
    # `column_builder.from_dataframe` accetta (df, gdf): per geospatial
    # df e gdf sono lo stesso oggetto (read_dataframe ritorna GeoDataFrame
    # con tabular access). Per formati non-geo, gdf=None.
    gdf_for_columns = df_preview if format_type in ('geojson', 'gpkg', 'shp', 'gdb', 'zip') else None
    columns = column_builder.from_dataframe(df_preview, gdf_for_columns)
    # H-4 (2026-05-26): row_count semantics esplicita via
    # `row_count_is_estimate`. Quando `total_row_count is None`, il
    # bounded preview non puo' calcolare il totale cheap (CSV/XLSX/
    # JSON-lines): `row_count` diventa `len(df_preview)` (lower-bound
    # = max_rows del bounded loader) e `is_estimate=True`. Quando il
    # formato espone metadata (parquet, geospatial via read_info),
    # `row_count` e' il totale reale e `is_estimate=False`. Il FE
    # deve interpretare `is_estimate=True` come "stima, totale reale
    # disponibile dopo upload definitivo".
    if total_row_count is not None:
        row_count = total_row_count
        row_count_is_estimate = False
    else:
        row_count = len(df_preview)
        row_count_is_estimate = True

    return {
        "columns": [{"name": c.name, "type": c.type} for c in columns],
        "row_count": row_count,
        "row_count_is_estimate": row_count_is_estimate,
        "format": format_type,
        "layers": [],
        "sheets": [],
    }
