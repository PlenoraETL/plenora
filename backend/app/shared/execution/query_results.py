"""Result contracts shared by query executors and persistence adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class PersistedQueryResult:
    result_id: Optional[str]
    output_path: Path
    storage_key: Optional[str] = None


@dataclass(frozen=True)
class StreamedQueryResult:
    result_id: Optional[str]
    rows_count: int
    columns: List[str]
    output_path: Optional[Path] = None
    storage_key: Optional[str] = None


__all__ = ["PersistedQueryResult", "StreamedQueryResult"]
