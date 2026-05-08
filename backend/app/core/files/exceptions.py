"""Domain exceptions for files."""

from __future__ import annotations

from typing import Literal, Optional


class FileSourceResolutionError(Exception):
    """Raised when resolving or materializing a file source fails."""

    def __init__(
        self,
        file_source_id: str,
        cause: Literal["lookup", "materialization"],
        reason: str,
        original_error: Optional[Exception] = None,
    ):
        self.file_source_id = file_source_id
        self.cause = cause
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f"Errore risoluzione file_source '{file_source_id}' "
            f"({cause}): {reason}"
        )


__all__ = ["FileSourceResolutionError"]
