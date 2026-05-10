"""Single-file upload parsing and file-source creation."""

from __future__ import annotations

import tempfile
import os
import sys
import zipfile
from pathlib import Path
from typing import Any, BinaryIO, Callable, List, Optional, Tuple

from app.core.files.storage_dispatch import BlobDispatchResult
from app.utils.mime_validator import MimeMismatchError


def mkdtemp_writable(*, dir: str | Path) -> Path:
    """Create an operation-owned temp dir that remains writable on Windows."""
    if sys.platform != "win32":
        return Path(tempfile.mkdtemp(dir=dir))

    parent = Path(dir)
    parent.mkdir(parents=True, exist_ok=True)
    for name in tempfile._get_candidate_names():
        path = parent / name
        try:
            os.mkdir(path, 0o777)
        except FileExistsError:
            continue
        return path
    raise FileExistsError(f"No usable temporary directory name in {parent}")


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
    list_layers: Callable[[Path], List[str]],
    list_sheets: Callable[[Path], List[str]],
    load_by_format: Callable[..., Tuple[Any, Optional[Any]]],
    column_builder_from_df: Callable[..., List[Any]],
    dispatch_blob_storage: Callable[[str, bytes, str], BlobDispatchResult],
    db_save_file_source: Callable[..., None],
    build_file_source: Callable[..., Any],
    cleanup: Callable[[Any], None],
    safe_extractall: Callable[..., None],
    zip_slip_error: type,
    temp_dir: str,
    default_group: str,
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> Any:
    """Crea file source da upload single-file (con parsing colonne).

    Vedi docstring del modulo per invarianti e collaborator.

    Returns:
        L'istanza DTO costruita da `build_file_source`.

    Raises:
        ValueError: su MIME block (riemesso da MimeMismatchError)
                       o su ZIP malformato / path traversal.
        Exception: dispatch_blob_storage / db_save_file_source
                       propagano le loro eccezioni (orphan guard).
    """
    file_id = generate_file_id(name)
    format_type = detect_format(filename)

    suffix = Path(filename).suffix or ".bin"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=temp_dir)
    file_content.seek(0)
    raw_data = file_content.read()
    tmp.write(raw_data)
    tmp.close()
    tmp_path = Path(tmp.name)

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
                try:
                    cleanup(tmp.name)
                except Exception as ce:
                    logger.debug(f"cleanup tmp upload (mime block) fallito: {ce}")
                raise ValueError(str(e)) from e
            raise

    zip_raw_data = raw_data
    zip_tmp_dir: Optional[Path] = None

    try:
        if format_type == 'zip':
            zip_tmp_dir = mkdtemp_writable(dir=temp_dir)
            try:
                with zipfile.ZipFile(tmp_path, 'r') as z:
                    safe_extractall(z, zip_tmp_dir)
            except zipfile.BadZipFile as e:
                raise ValueError(f"File ZIP non valido o corrotto: {e}") from e
            except zip_slip_error as e:
                raise ValueError(f"File ZIP rifiutato (path traversal): {e}") from e
            extracted_files = [
                f for f in zip_tmp_dir.rglob("*")
                if f.is_file() or (f.is_dir() and f.suffix.lower() == ".gdb")
            ]
            for ext in ['.shp', '.gpkg', '.gdb', '.geojson', '.csv', '.xlsx']:
                match = next(
                    (f for f in extracted_files if f.suffix.lower() == ext), None,
                )
                if match:
                    tmp_path = match
                    format_type = detect_format(match.name)
                    filename = match.name
                    if format_type in ('shp', 'gdb'):
                        raw_data = zip_raw_data
                    else:
                        raw_data = tmp_path.read_bytes()
                    break

        if format_type in ['gdb', 'gpkg']:
            layers = list_layers(tmp_path)
            if not layer and len(layers) == 1:
                layer = layers[0]
        elif format_type == 'xlsx':
            sheets = list_sheets(tmp_path)
            if not sheet and len(sheets) == 1:
                sheet = sheets[0]

        # Schema inference is best-effort; upload still persists on parser failures.
        columns: List[Any] = []
        row_count = 0
        crs: Optional[str] = None
        try:
            df, gdf = load_by_format(tmp_path, format_type, layer, sheet)
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

    finally:
        if zip_tmp_dir is not None:
            try:
                cleanup(zip_tmp_dir)
            except Exception as e:
                logger.debug(
                    f"cleanup zip_tmp_dir fallito ({zip_tmp_dir}): {e}"
                )
        try:
            cleanup(tmp.name)
        except Exception as e:
            logger.debug(f"cleanup tmp upload fallito ({tmp.name}): {e}")
