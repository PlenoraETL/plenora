"""Preview temporanea bounded da stream, senza persistenza applicativa.

Il body FastAPI resta seekable; eventuali member ZIP passano da spool
automatico RAM/disco e sono rimossi alla chiusura.
"""

from __future__ import annotations

import logging
import zipfile
from typing import BinaryIO, Optional
from typing_extensions import TypedDict

from app.core.dataframes import load_dispatch as load_dispatch_module
from app.core.dataframes import preview_loader as preview_loader_module
from app.core.io import geospatial as geospatial_io_module
from app.core.io.bounded_streams import read_prefix
from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.core.io.zip_guards import (
    open_zip_stream_safe,
    pick_zip_member_by_extension,
    spool_zip_member,
)


_PREVIEW_INNER_EXTENSIONS = ('.gpkg', '.gdb', '.shp')


_logger_module = logging.getLogger(__name__)


class FilePreviewTempColumnPayload(TypedDict):
    name: str
    type: str


class FilePreviewTempPayload(TypedDict):
    columns: list[FilePreviewTempColumnPayload]
    row_count: int
    # True quando row_count e' un lower-bound del totale: il formato non espone
    # metadata cheap e la bounded preview carica solo `max_rows`.
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
        `row_count` puo' essere `None` quando il formato
        non espone metadata cheap (csv, jsonlines, xlsx); il frontend
        deve interpretare `None` come "row count non disponibile in
        preview, riprovare dopo upload definitivo".

    Raises:
        ValueError: formato non supportato o MIME mismatch (block-only).
    """
    file_content.seek(0)
    source_stream = file_content
    owned_stream = None
    try:
        validate_no_executable(content=read_prefix(source_stream), filename=filename)
    except MimeMismatchError as e:
        logger.warning(
            f"preview_file_temp bloccato (MIME): filename={filename!r}: {e}"
        )
        raise ValueError(str(e)) from e

    format_type = format_detector.detect(filename)
    if format_type == 'unknown':
        raise ValueError(f"Formato file non supportato: {filename}")

    if format_type == 'zip':
        try:
            with open_zip_stream_safe(file_content) as z:
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
                    source_stream = file_content
                else:
                    owned_stream = spool_zip_member(z, inner_name)
                    source_stream = owned_stream
                    # SAFETY: defense-in-depth MIME su inner file.
                    try:
                        validate_no_executable(
                            content=read_prefix(source_stream),
                            filename=inner_display,
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
        available_layers = geospatial_io_module.list_vector_layers_stream(
            source_stream
        )
        if len(available_layers) > 1:
            if owned_stream is not None:
                owned_stream.close()
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
        available_sheets = load_dispatch_module.list_excel_sheets_from_stream(
            source_stream
        )
        if len(available_sheets) > 1:
            if owned_stream is not None:
                owned_stream.close()
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
    # Bounded preview: carica al massimo `max_rows` righe/feature invece
    # del DataFrame intero. Per CSV/XLSX/JSON-lines il row_count totale non
    # e' disponibile cheap (None); per parquet e geospatial viene da metadata.
    source_stream.seek(0)
    df_preview, total_row_count = preview_loader_module.preview_dataframe_from_stream(
        source_stream,
        format_type,
        layer=None if is_sheet else layer,
        sheet=sheet if is_sheet else None,
    )
    # `column_builder.from_dataframe` accetta (df, gdf): per geospatial
    # df e gdf sono lo stesso oggetto (read_dataframe ritorna GeoDataFrame
    # con tabular access). Per formati non-geo, gdf=None.
    gdf_for_columns = df_preview if format_type in ('geojson', 'gpkg', 'shp', 'gdb', 'zip') else None
    columns = column_builder.from_dataframe(df_preview, gdf_for_columns)
    # Semantica row_count esplicita via `row_count_is_estimate`: quando
    # `total_row_count is None`, il
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

    result = {
        "columns": [{"name": c.name, "type": c.type} for c in columns],
        "row_count": row_count,
        "row_count_is_estimate": row_count_is_estimate,
        "format": format_type,
        "layers": [],
        "sheets": [],
    }
    if owned_stream is not None:
        owned_stream.close()
    return result
