"""Attachment boundary helpers for ticket endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Optional

from fastapi import HTTPException

from app.shared.contracts.payloads import HeadersMap
from app.utils.mime_validator import MimeMismatchError, validate_upload


def validate_attachment_upload(
    *,
    content: bytes,
    filename: str,
    max_bytes: int,
    size_bytes: Optional[int] = None,
    allowed_extensions: set[str],
    logger,
    username: str,
) -> None:
    """Validate ticket attachment size, emptiness and content MIME."""
    actual_size = len(content) if size_bytes is None else int(size_bytes)
    if actual_size > max_bytes:
        raise HTTPException(
            413,
            f"Allegato troppo grande ({actual_size} bytes). "
            f"Limite: {max_bytes} bytes",
        )
    if actual_size == 0:
        raise HTTPException(400, "File allegato vuoto")

    try:
        validate_upload(
            content=content,
            filename=filename,
            allowed_extensions=allowed_extensions,
        )
    except MimeMismatchError as exc:
        logger.warning(
            f"Upload allegato ticket rifiutato (MIME): {filename!r} "
            f"by={username}: {exc}",
            op="ticket_attach_reject",
        )
        raise HTTPException(400, str(exc))


def download_headers(
    *,
    filename: str,
    fmt: str,
    blob_size: Optional[int],
    allowed_extensions: Mapping[str, Optional[str]],
) -> tuple[str, HeadersMap]:
    """Return media type and headers for attachment download."""
    mime = allowed_extensions.get(fmt) or "application/octet-stream"
    disposition = "inline" if mime.startswith("image/") else "attachment"
    safe_name = filename.replace('"', "").replace("\r", "").replace("\n", "")
    headers: HeadersMap = {
        "Content-Disposition": f'{disposition}; filename="{safe_name}"',
    }
    if blob_size is not None and blob_size > 0:
        headers["Content-Length"] = str(blob_size)
    return mime, headers
