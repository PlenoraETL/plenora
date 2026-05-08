"""Domain exceptions for intermediate persistence."""

from __future__ import annotations

from typing import Optional


class IntermediateSettingsError(ValueError):
    """Invalid intermediate persistence settings."""


class IntermediateWriteError(Exception):
    """Raised when persisting an intermediate node fails."""

    def __init__(
        self,
        run_id: str,
        node_id: str,
        original_error: Optional[Exception] = None,
    ):
        self.run_id = run_id
        self.node_id = node_id
        self.original_error = original_error
        err_type = (
            type(original_error).__name__ if original_error else "unknown"
        )
        super().__init__(
            f"Intermediate write fallito per run={run_id} node={node_id} "
            f"({err_type})"
        )


__all__ = [
    "IntermediateSettingsError",
    "IntermediateWriteError",
]
