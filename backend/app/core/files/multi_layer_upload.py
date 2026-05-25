"""Multi-layer and multi-sheet upload parsing.

Post-Phase4 refactor: RAM-only. Niente piu' NamedTemporaryFile /
mkdtemp_writable / safe_extractall — il payload upload e' navigato
in memoria via `zipfile.ZipFile(BytesIO(...))` e i parser bytes-first
di `app.core.dataframes.load_dispatch`.
"""

from __future__ import annotations

import uuid
import zipfile
from io import BytesIO
from typing import Any, BinaryIO, Callable, Dict, List, Optional

from app.core.files.db import FileSourceRow
from app.core.files.storage_dispatch import BlobDispatchResult
from app.utils.mime_validator import MimeMismatchError, validate_no_executable
from app.utils.zip_safe import (
    pick_zip_member_by_extension,
    validate_zip_bomb_limits,
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

    RAM-only: tutto il payload e' processato in memoria. Vedi docstring
    del modulo per invarianti e collaborator.

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
    from app.core.dataframes.load_dispatch import load_by_format_bytes

    if mime_validate is None:
        mime_validate = validate_no_executable

    if not layers_config:
        raise ValueError("layers_config non puo essere vuota")

    file_content.seek(0)
    raw_data = file_content.read()

    # MIME validation runs before parse, blob dispatch, or DB writes.
    try:
        mime_validate(content=raw_data, filename=filename or name)
    except Exception as e:
        if isinstance(e, MimeMismatchError):
            logger.warning(
                f"create_multi_layer_from_upload bloccato (MIME): "
                f"name={name!r} filename={filename!r}: {e}"
            )
            raise ValueError(str(e)) from e
        raise

    format_type = detect_format(filename)
    # `inner_bytes` e' il payload usato per la schema inference (per ZIP
    # con .gdb interno = lo ZIP intero che pyogrio legge via /vsizip;
    # per ZIP con .gpkg interno = i bytes del gpkg estratti dallo ZIP).
    inner_bytes = raw_data

    if format_type == 'zip':
        # Pre-fix: estraeva su mkdtemp + safe_extractall, navigava la dir
        # per estensione, leggeva i bytes del path. Post-fix: navigazione
        # del ZIP in RAM via zipfile.ZipFile(BytesIO). NB: SOLO .gpkg /
        # .gdb (no .shp/.csv come create_from_upload).
        outer_filename = filename
        try:
            with zipfile.ZipFile(BytesIO(raw_data), 'r') as z:
                # SAFETY: zip bomb guard prima di `z.read()`.
                validate_zip_bomb_limits(z)

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
                    # .gdb e' una directory: pyogrio legge lo ZIP intero
                    # via /vsizip (raw_data resta lo ZIP); inner_bytes
                    # punta allo stesso ZIP per parse.
                    inner_bytes = raw_data
                else:
                    # .gpkg single-file: estrai i bytes dal ZIP. Per il
                    # blob di storage manteniamo lo ZIP originale (raw_data)
                    # — nota: la versione pre-fix invece sostituiva il
                    # blob con i bytes del file estratto. Mantengo il
                    # comportamento pre-fix per non rompere downstream:
                    raw_data = z.read(inner_name)
                    inner_bytes = raw_data
                    # SAFETY: ri-valida MIME sull'inner file (defense-in-depth
                    # contro ZIP-with-trojan-inside).
                    try:
                        mime_validate(
                            content=inner_bytes, filename=inner_display,
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

    # BE-HARDEN-001C E1 - shared blob dispatch
    # 1 chiamata a dispatch_blob_storage PRIMA del loop con
    # `storage_key_id=batch_id`. Le N row in object_storage path
    # condividono lo stesso storage_key. In BYTEA path le N row
    # hanno gli stessi `data` bytes
    # Failure pre-loop -> nessuna row, nessun cleanup necessario
    # orphan guard interno al dispatch.
    batch_id = f"batch_{uuid.uuid4().hex}"
    shared_blob_kwargs = dispatch_blob_storage(
        batch_id, raw_data, filename,
        storage_key_id=batch_id,
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
                df, gdf = load_by_format_bytes(
                    inner_bytes, format_type, layer=None, sheet=layer,
                )
            else:
                df, gdf = load_by_format_bytes(
                    inner_bytes, format_type, layer=layer, sheet=None,
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

        rows.append({
            "file_id": file_id,
            "group_id": group_id or default_group,
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

    return created
