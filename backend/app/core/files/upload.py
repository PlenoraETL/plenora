"""Single-file upload parsing and file-source creation.

Post-Phase4 refactor: il payload upload e' processato interamente in
RAM. Niente piu' NamedTemporaryFile / mkdtemp / safe_extractall:
- ZIP navigato via `zipfile.ZipFile(BytesIO(raw_data))` + `zf.read`
  per estrarre il singolo inner file in bytes.
- Schema inference via `load_by_format_bytes(raw_data, format_type,
  ...)` (vedi `app.core.dataframes.load_dispatch`).
- Layer/sheet listing via `list_vector_layers(bytes)` (io.geospatial)
  o `list_excel_sheets_from_bytes(bytes)`.
"""

from __future__ import annotations

import zipfile
from typing import Any, BinaryIO, Callable, List, Optional

from app.core.dataframes import load_dispatch as load_dispatch_module
from app.core.io import geospatial as geospatial_io_module
from app.core.resources.blob_dispatch import BlobDispatchResult
from app.utils.mime_validator import MimeMismatchError
from app.core.io.zip_guards import (
    open_zip_bytes_safe,
    pick_zip_member_by_extension,
)


_SUPPORTED_INNER_EXTENSIONS = (
    '.shp', '.gpkg', '.gdb', '.geojson', '.csv', '.xlsx',
)


def create_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    layer: Optional[str] = None,
    sheet: Optional[str] = None,
    selected_columns: Optional[List[str]] = None,
    srid: Optional[int] = None,
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    detect_format: Callable[[str], str],
    column_builder_from_df: Callable[..., List[Any]],
    dispatch_blob_storage: Callable[[str, bytes, str], BlobDispatchResult],
    db_save_file_source: Callable[..., None],
    build_file_source: Callable[..., Any],
    default_group: str,
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> Any:
    """Crea file source da upload single-file (con parsing colonne).

    RAM-only: l'intero payload upload e' processato in memoria. Vedi
    docstring del modulo per invarianti e collaborator.

    Returns:
        L'istanza DTO costruita da `build_file_source`.

    Raises:
        ValueError: su MIME block (riemesso da MimeMismatchError)
                       o su ZIP malformato / nessun file supportato
                       nel ZIP.
        Exception: dispatch_blob_storage / db_save_file_source
                       propagano le loro eccezioni (orphan guard).
    """
    file_id = generate_file_id(name)
    format_type = detect_format(filename)

    file_content.seek(0)
    raw_data = file_content.read()

    # MIME validation runs before parsing, blob dispatch, or DB writes.
    if mime_validate is not None:
        try:
            mime_validate(content=raw_data, filename=filename or name)
        except Exception as e:
            if isinstance(e, MimeMismatchError):
                logger.warning(
                    f"create_from_upload bloccato (MIME): "
                    f"name={name!r} filename={filename!r}: {e}"
                )
                raise ValueError(str(e)) from e
            raise

    zip_raw_data = raw_data
    # `inner_bytes` e' il payload usato per la schema inference (per ZIP
    # geospatial = lo ZIP intero che pyogrio sa navigare via /vsizip;
    # per ZIP single-file tabular = i bytes del file interno).
    inner_bytes = raw_data

    if format_type == 'zip':
        # Pre-fix: estraeva il ZIP su mkdtemp + safe_extractall, poi
        # navigava la dir per estensione, poi rileggeva i bytes del
        # file estratto. Post-fix: zipfile.ZipFile(BytesIO) naviga
        # in RAM, e per shapefile/gdb pyogrio gestisce direttamente
        # lo ZIP via /vsizip (vedi `_load_dataframe_from_bytes`).
        outer_filename = filename
        try:
            # `open_zip_bytes_safe` valida i cap zip-bomb PRIMA di yield:
            # un member da 10GB uncompressed esaurirebbe la RAM del worker.
            # Stesso cap del path-based legacy (env ZIP_MAX_UNCOMPRESSED_BYTES).
            with open_zip_bytes_safe(raw_data) as z:
                names = z.namelist()
                inner_name, inner_format, inner_display = pick_zip_member_by_extension(
                    names,
                    extensions=_SUPPORTED_INNER_EXTENSIONS,
                    detect_format=detect_format,
                )

                if inner_name is None:
                    raise ValueError(
                        f"ZIP non riconosciuto: nessun file con "
                        f"estensione supportata. Contenuto: "
                        f"{names[:10]}{'...' if len(names) > 10 else ''}"
                    )

                format_type = inner_format
                filename = inner_display
                if inner_format in ('shp', 'gdb'):
                    # Multi-file: pyogrio legge lo ZIP intero via /vsizip.
                    inner_bytes = zip_raw_data
                else:
                    # Single-file in ZIP: estrai i bytes dell'inner file.
                    inner_bytes = z.read(inner_name)
                    # SAFETY: ri-valida MIME sull'inner file. L'outer ZIP
                    # e' gia' passato per `mime_validate` sopra, ma un
                    # ZIP innocuo puo' contenere un .csv-named-but-PE.
                    if mime_validate is not None:
                        try:
                            mime_validate(
                                content=inner_bytes, filename=inner_display,
                            )
                        except MimeMismatchError as e:
                            logger.warning(
                                f"create_from_upload bloccato (MIME inner): "
                                f"zip={outer_filename!r} "
                                f"inner={inner_display!r}: {e}"
                            )
                            raise ValueError(str(e)) from e
        except zipfile.BadZipFile as e:
            raise ValueError(f"File ZIP non valido o corrotto: {e}") from e

    # Layer/sheet auto-pick (RAM-only) -- listing da bytes, niente piu'
    # chiamata a list_layers(tmp_path) / list_sheets(tmp_path).
    if format_type in ('gdb', 'gpkg') and not layer:
        try:
            layers = geospatial_io_module.list_vector_layers(inner_bytes)
            if len(layers) == 1:
                layer = layers[0]
        except Exception as e:
            logger.debug(f"list_vector_layers fallito: {e}")
    elif format_type == 'xlsx' and not sheet:
        try:
            sheets = load_dispatch_module.list_excel_sheets_from_bytes(inner_bytes)
            if len(sheets) == 1:
                sheet = sheets[0]
        except Exception as e:
            logger.debug(f"list_excel_sheets_from_bytes fallito: {e}")

    # Schema inference best-effort via bytes -> df/gdf (no filesystem).
    columns: List[Any] = []
    row_count = 0
    crs: Optional[str] = None
    try:
        df, gdf = load_dispatch_module.load_by_format_bytes(
            inner_bytes, format_type, layer=layer, sheet=sheet,
        )
        if selected_columns:
            valid_cols = [c for c in selected_columns if c in df.columns]
            if valid_cols:
                df = df[valid_cols]
        columns = column_builder_from_df(df, gdf, srid)
        row_count = len(df)
        if gdf is not None and hasattr(gdf, 'crs') and gdf.crs:
            epsg = gdf.crs.to_epsg()
            if epsg:
                crs = f"EPSG:{epsg}"
            else:
                auth = gdf.crs.to_authority()
                crs = f"{auth[0]}:{auth[1]}" if auth else str(gdf.crs)
        elif srid:
            crs = f"EPSG:{srid}"
    except Exception as e:
        logger.warning(f"Errore inferenza colonne: {e}")

    now = time_now()
    columns_dicts = [
        {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
        for c in columns
    ]
    file_source = build_file_source(
        id=file_id,
        name=name,
        description=description,
        original_filename=filename,
        format=format_type,
        layer=layer,
        sheet=sheet,
        columns=columns_dicts,
        row_count=row_count,
        file_size=len(raw_data),
        crs=crs,
        group_id=group_id or default_group,
        created_at=now,
        updated_at=now,
    )

    metadata = (
        file_source.model_dump(mode='json')
        if hasattr(file_source, 'model_dump')
        else {}
    )

    # Blob dispatch must complete before DB save to avoid orphan records.
    blob_kwargs = dispatch_blob_storage(file_id, raw_data, filename)

    db_save_file_source(
        file_id=file_id,
        group_id=group_id or default_group,
        name=name,
        original_filename=filename,
        fmt=format_type,
        metadata=metadata,
        **blob_kwargs,
    )

    logger.info(f"FileSource creato: {file_id} ({len(raw_data)} bytes)")
    return file_source
