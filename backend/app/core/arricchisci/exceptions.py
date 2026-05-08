"""Domain exceptions for arricchisci execution."""

from __future__ import annotations

from typing import Optional


class ExecutionContextError(Exception):
    """Raised when arricchisci execution context initialization fails."""

    def __init__(
        self,
        project_id: str,
        reason: str,
        original_error: Optional[Exception] = None,
    ):
        self.project_id = project_id
        self.reason = reason
        self.original_error = original_error
        super().__init__(
            f"Errore inizializzazione contesto esecuzione per "
            f"progetto '{project_id}': {reason}"
        )


__all__ = ["ExecutionContextError"]
