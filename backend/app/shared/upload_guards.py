"""
Helper condivisi per validare gli `UploadFile` FastAPI prima di

leggerne il contenuto in RAM.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import HTTPException

from app.config import settings

if TYPE_CHECKING:
    from fastapi import UploadFile


_DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def enforce_upload_size_limit(
    file: "UploadFile",
    max_bytes: Optional[int] = None,
) -> int:
    """Rifiuta con 413 gli upload che superano `max_bytes`.

    Determina la dimensione **senza leggere il contenuto in RAM**: usa
    `seek(end) -> tell -> seek(0)`, lasciando il cursore a 0 per il
    caller. Questo evita il pattern fragile "MIME validate via `read`
    poi enforce size dopo": un attaccante autenticato puo' altrimenti
    forzare la lettura di N GB in RAM (DoS interno credenziato) prima
    del rifiuto.

    Args:
        file: oggetto FastAPI `UploadFile` (o equivalente con `.file`
            esponente seek/tell).
        max_bytes: cap massimo in byte. Se None (default), letto da
            `settings.MAX_FILE_SIZE` con fallback 100MB
            (`app/config.py:139`).

    Returns:
        dimensione in byte se sotto soglia.

    Raises:
        HTTPException(413) se la dimensione supera il cap.
    """
    if max_bytes is None:
        max_bytes = settings.get("MAX_FILE_SIZE", _DEFAULT_MAX_FILE_SIZE)

    file.file.seek(0, 2)  # seek to end
    file_size = file.file.tell()
    file.file.seek(0)

    if file_size > max_bytes:
        max_mb = max_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=413,
            detail=(
                f"File troppo grande ({file_size // (1024 * 1024)}MB). "
                f"Limite massimo: {max_mb}MB."
            ),
        )
    return file_size
