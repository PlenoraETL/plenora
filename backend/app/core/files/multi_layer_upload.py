"""Multi-layer/multi-sheet upload parsing da stream seekable bounded."""

from __future__ import annotations

import uuid
import zipfile
from typing import Any, BinaryIO, Callable, Dict, List, Optional

from app.core.files.db import FileSourceRow
from app.core.resources.blob_dispatch import BlobDispatchResult
from app.core.io.bounded_streams import read_prefix, stream_size
from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.core.io.zip_guards import (
    open_zip_stream_safe,
    pick_zip_member_by_extension,
    spool_zip_member,
)


_MULTI_LAYER_INNER_EXTENSIONS = ('.gpkg', '.gdb')


def create_multi_layer_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    layers_config: Optional[List[Dict[str, Any]]] = None,
    srid: Optional[int] = None,
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    detect_format: Callable[[str], str],
    column_builder_from_df: Callable[..., List[Any]],
    dispatch_blob_storage: Callable[..., BlobDispatchResult],
    db_save_file_sources_batch: Callable[[List[FileSourceRow]], None],
    delete_blob: Callable[[str], None],
    build_file_source: Callable[..., Any],
    default_group: str,
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> List[Any]:
    """Crea N FileSource da un file multi-layer (GPKG/GDB) o multi-sheet (XLSX).

    Il BLOB viene riusato come file-like tra i layer, senza copie complete.

    Args:
        layers_config: lista di dict con chiavi opzionali:
            - "layer": nome layer (per GPKG/GDB);
            - "sheet": nome sheet (per XLSX);
            - "selected_columns": lista colonne da importare (opzionale).

    Returns:
        Lista di FileSource costruiti via build_file_source (uno per layer
        configurato).

    Raises:
        ValueError: layers_config vuoto/None, MIME block, ZIP malformato
                    o nessun file supportato nel ZIP.
        Exception: dispatch_blob_storage / db_save_file_sources_batch
                    propagano la loro eccezione. atomico (E3): rollback
                    DB + cleanup compensativo del shared storage_key
                    best-effort.
    """
    from app.core.dataframes.load_dispatch import load_by_format_stream

    if mime_validate is None:
        mime_validate = validate_no_executable

    if not layers_config:
        raise ValueError("layers_config non puo essere vuota")

    file_content.seek(0)
    source_stream = file_content
    source_size = stream_size(source_stream)
    owned_stream = None

    # MIME validation runs before parse, blob dispatch, or DB writes.
    try:
        mime_validate(content=read_prefix(source_stream), filename=filename or name)
    except Exception as e:
        if isinstance(e, MimeMismatchError):
            logger.warning(
                f"create_multi_layer_from_upload bloccato (MIME): "
                f"name={name!r} filename={filename!r}: {e}"
            )
            raise ValueError(str(e)) from e
        raise

    format_type = detect_format(filename)
    if format_type == 'zip':
        # Navigazione seekable; decompressione member a chunk nello spool.
        # NB: SOLO .gpkg / .gdb (no .shp/.csv come create_from_upload).
        outer_filename = filename
        try:
            # `open_zip_bytes_safe` valida i cap zip-bomb PRIMA di yield.
            with open_zip_stream_safe(file_content) as z:
                names = z.namelist()
                inner_name, inner_format, inner_display = pick_zip_member_by_extension(
                    names,
                    extensions=_MULTI_LAYER_INNER_EXTENSIONS,
                    detect_format=detect_format,
                )

                if inner_name is None:
                    raise ValueError(
                        f"ZIP non riconosciuto: nessun .gpkg/.gdb. "
                        f"Contenuto: "
                        f"{names[:10]}{'...' if len(names) > 10 else ''}"
                    )

                format_type = inner_format
                filename = inner_display
                if inner_format == 'gdb':
                    source_stream = file_content
                    source_size = stream_size(file_content)
                else:
                    owned_stream = spool_zip_member(z, inner_name)
                    source_stream = owned_stream
                    source_size = stream_size(source_stream)
                    # SAFETY: ri-valida MIME sull'inner file (defense-in-depth
                    # contro ZIP-with-trojan-inside).
                    try:
                        mime_validate(
                            content=read_prefix(source_stream),
                            filename=inner_display,
                        )
                    except MimeMismatchError as e:
                        logger.warning(
                            f"create_multi_layer_from_upload bloccato "
                            f"(MIME inner): zip={outer_filename!r} "
                            f"inner={inner_display!r}: {e}"
                        )
                        raise ValueError(str(e)) from e
        except zipfile.BadZipFile as e:
            raise ValueError(f"File ZIP non valido o corrotto: {e}") from e

    # Determina se stiamo lavorando con layer (gpkg/gdb) o sheet (xlsx).
    is_sheet_based = format_type in ['xlsx', 'xls']

    now = time_now()
    resolved_group_id = group_id or default_group

    # BE-HARDEN-001C E1 - shared blob dispatch
    # 1 chiamata a dispatch_blob_storage PRIMA del loop con
    # `storage_key_id=batch_id`. Le N row in object_storage path
    # condividono lo stesso storage_key. In BYTEA path le N row
    # hanno gli stessi `data` bytes
    # Failure pre-loop -> nessuna row, nessun cleanup necessario
    # orphan guard interno al dispatch.
    batch_id = f"batch_{uuid.uuid4().hex}"
    shared_blob_kwargs = dispatch_blob_storage(
        batch_id, source_stream, filename,
        group_id=resolved_group_id,
        storage_key_id=batch_id,
        size_bytes=source_size,
    )

    # Loop di staging: parse + DTO build per ogni layer, ZERO side
    # effect DB. La persistenza avviene UNA volta sola dopo il loop
    # via db_save_file_sources_batch (E3 atomic transaction).
    created: List[Any] = []
    rows: List[FileSourceRow] = []
    for layer_cfg in layers_config:
        layer = layer_cfg.get("layer") or layer_cfg.get("sheet")
        selected_columns = layer_cfg.get("selected_columns")

        safe_layer = (
            layer.replace(" ", "_").replace("-", "_") if layer else "default"
        )
        file_source_name = f"{name}_{safe_layer}"
        file_id = generate_file_id(file_source_name)

        columns: List[Any] = []
        row_count = 0
        crs: Optional[str] = None
        try:
            if is_sheet_based:
                df, gdf = load_by_format_stream(
                    source_stream, format_type, layer=None, sheet=layer,
                )
            else:
                df, gdf = load_by_format_stream(
                    source_stream, format_type, layer=layer, sheet=None,
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
            logger.warning(f"Errore inferenza colonne layer '{layer}': {e}")

        columns_dicts = [
            {"name": c.name, "type": c.type} if not isinstance(c, dict) else c
            for c in columns
        ]

        file_source = build_file_source(
            id=file_id,
            name=file_source_name,
            description=description,
            original_filename=filename,
            format=format_type,
            layer=None if is_sheet_based else layer,
            sheet=layer if is_sheet_based else None,
            columns=columns_dicts,
            row_count=row_count,
            file_size=source_size,
            crs=crs,
            group_id=resolved_group_id,
            created_at=now,
            updated_at=now,
        )

        metadata = (
            file_source.model_dump(mode='json')
            if hasattr(file_source, 'model_dump')
            else {}
        )

        rows.append({
            "file_id": file_id,
            "group_id": resolved_group_id,
            "name": file_source_name,
            "original_filename": filename,
            "fmt": format_type,
            "metadata": metadata,
            **shared_blob_kwargs,
        })
        created.append(file_source)

    # BE-HARDEN-001C E3 - all-or-nothing
    # Una sola transazione DB attorno alle N INSERT (via
    # save_file_sources_batch). Se solleva, cleanup compensativo
    # del shared storage_key (best-effort) prima che la eccezione
    # DB propaghi. Un eventuale fail del cleanup -> log strutturato
    # `event=orphan_blob_cleanup_failed` SENZA mascherare la causa root.
    committed = False
    try:
        db_save_file_sources_batch(rows)
        committed = True
    finally:
        if not committed:
            shared_key = shared_blob_kwargs.get("storage_key")
            if shared_key:
                try:
                    delete_blob(shared_key)
                except Exception as cleanup_err:
                    logger.error(
                        f"event=orphan_blob_cleanup_failed "
                        f"batch_id={batch_id} "
                        f"storage_key={shared_key!r} "
                        f"error={cleanup_err!r}"
                    )

    for fs in created:
        logger.info(
            f"FileSource multi-layer creato: {fs.id} "
            f"layer={getattr(fs, 'layer', None)!r} "
            f"sheet={getattr(fs, 'sheet', None)!r}"
        )

    if owned_stream is not None:
        owned_stream.close()

    return created
