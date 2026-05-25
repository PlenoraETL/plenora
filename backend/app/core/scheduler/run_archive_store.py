"""Typed primitives for scheduled-run history.

Post-cleanup `ScheduleArtifactStore`: il modulo NON contiene piu'
boundary filesystem reads (`list_runs`, `get_run_dir`, `latest_run_dir`,
`first_file_in`). Conserva solo i tipi value (`RunInfo`, `RunFileInfo`)
e l'helper di token (`schedule_short`).

L'enumerazione delle run e' ora unica responsabilita' di
`ScheduleRunsCatalog`, che la deriva dal `schedule_logs` event stream.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True)
class RunFileInfo:
    """Metadata for a file inside an archived run directory."""

    name: str
    size_bytes: int
    result_id: Optional[str] = None


@dataclass(frozen=True)
class RunInfo:
    """Snapshot of a scheduled run.

    `path` resta come Optional[Path] per compat di shape ma post-cleanup
    e' sempre `None` (niente piu' archive filesystem).
    """

    timestamp: str
    path: Optional[Path]
    files: Tuple[RunFileInfo, ...] = field(default_factory=tuple)
    result_id: Optional[str] = None

    @property
    def total_size_bytes(self) -> int:
        return sum(f.size_bytes for f in self.files)


def schedule_short(schedule_id: str) -> str:
    """Return the stable folder token for a schedule id.

    Preservato per compat con i callsite legacy che ancora costruiscono
    path-like reference; il token e' usato anche come segmento URL nei
    download endpoint.
    """
    return schedule_id.replace("schedule_", "")[:12]


__all__ = [
    "RunFileInfo",
    "RunInfo",
    "schedule_short",
]
