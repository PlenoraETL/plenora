"""
Raw blob upload helper for file sources.

Keeps raw upload orchestration isolated from preview and multi-layer parsing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO, Callable, Optional

from app.core.files.storage_dispatch import BlobDispatchResult


def create_raw_from_upload(
    file_content: BinaryIO,
    filename: str,
    name: str,
    description: str = "",
    group_id: Optional[str] = None,
    *,
    generate_file_id: Callable[[str], str],
    time_now: Callable[[], Any],
    dispatch_blob_storage: Callable[[str, bytes, str], BlobDispatchResult],
    db_save_file_source: Callable[..., None],
    default_group: str,
    build_file_source: Callable[..., Any],
    logger,
    mime_validate: Optional[Callable[..., None]] = None,
) -> Any:
    """Create a raw file source blob without parsing its content."""
    if mime_validate is None:
        from app.utils.mime_validator import validate_no_executable

        mime_validate = validate_no_executable

    file_id = generate_file_id(name)
    file_content.seek(0)
    raw_data = file_content.read()

    try:
        mime_validate(content=raw_data, filename=filename or name)
    except Exception as exc:
        from app.utils.mime_validator import MimeMismatchError

        if isinstance(exc, MimeMismatchError):
            logger.warning(
                f"create_raw_from_upload bloccato (MIME): "
                f"name={name!r} filename={filename!r}: {exc}"
            )
            raise ValueError(str(exc)) from exc
        raise

    file_ext = Path(filename).suffix.lstrip(".").lower() or "bin"
    now = time_now()
    resolved_group_id = group_id or default_group

    file_source = build_file_source(
        id=file_id,
        name=name,
        description=description,
        original_filename=filename,
        format=file_ext,
        layer=None,
        sheet=None,
        columns=[],
        row_count=0,
        file_size=len(raw_data),
        crs=None,
        group_id=resolved_group_id,
        created_at=now,
        updated_at=now,
    )

    metadata = (
        file_source.model_dump(mode="json")
        if hasattr(file_source, "model_dump")
        else {}
    )

    blob_kwargs = dispatch_blob_storage(file_id, raw_data, filename)
    db_save_file_source(
        file_id=file_id,
        group_id=resolved_group_id,
        name=name,
        original_filename=filename,
        fmt=file_ext,
        metadata=metadata,
        **blob_kwargs,
    )

    logger.info(
        f"FileSource raw creato: {file_id} format={file_ext} "
        f"({len(raw_data)} bytes)"
    )
    return file_source


__all__ = ["create_raw_from_upload"]
