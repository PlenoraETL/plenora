"""Resource boundary exceptions."""

from __future__ import annotations


class MemoryLimitExceededError(Exception):
    """Raised when an input exceeds the configured processing memory cap."""

    def __init__(
        self,
        observed_bytes: int,
        limit_bytes: int,
        path_basename: str,
        stage: str,
    ) -> None:
        self.observed_bytes = int(observed_bytes)
        self.limit_bytes = int(limit_bytes)
        self.path_basename = str(path_basename or "")
        self.stage = str(stage or "")
        super().__init__(
            f"Memory limit exceeded in stage '{self.stage}' for "
            f"'{self.path_basename}': observed={self.observed_bytes} "
            f"bytes, limit={self.limit_bytes} bytes."
        )


__all__ = ["MemoryLimitExceededError"]
