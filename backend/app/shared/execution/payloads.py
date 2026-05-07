"""Typed payload value objects for canvas execution results.

BE-GO-READY-ARCH-060-W3-EXECUTION-PAYLOAD-MODULE-SPLIT.

Moved from ``executor_protocol.py`` to keep that module focused on the
``ExecutionResult`` / ``CanvasExecutorProtocol`` contract surface.

This module owns:

- ``ArtifactPayload`` - typed representation of common artifact result keys
  shared by all three executors (estrai / manipola / arricchisci).
- ``_check_payload_safe`` - internal guard used by ``ExecutionResult.__post_init__``
  to reject DataFrames, bytes, and file-like streams without importing Pandas.
- ``_UNSAFE_PAYLOAD_MODULES`` - frozenset of module-name prefixes that signal
  heavy DataFrame runtimes.

Boundary
========
Leaf contract module. Must NOT import:

- ``fastapi`` / ``starlette`` (no HTTP);
- ``app.db.*`` / ``psycopg2`` / ``redis`` (no I/O);
- ``pandas`` / ``geopandas`` / ``polars`` (no DataFrame);
- ``app.core.*`` (consumed by core, not vice-versa).

Go-ready mapping
================
::

    type ArtifactPayload struct {
        ResultID   *string
        OutputPath *string
        Filename   *string
        Format     *string
        RowsCount  *int
        RunID      *string
    }
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


__all__ = ["ArtifactPayload"]


# Payload safety guard (used by ExecutionResult.__post_init__)

# Module-name prefixes that signal a heavy DataFrame runtime.
# Checked by string comparison - no import of these libraries.
_UNSAFE_PAYLOAD_MODULES = frozenset({"pandas", "geopandas", "polars"})


def _check_payload_safe(payload: Dict[str, Any]) -> None:
    """Raise ``ValueError`` if any top-level payload value is unsafe.

    Unsafe: DataFrame/Series (pandas/geopandas/polars), raw ``bytes`` /
    ``bytearray``, or file-like streams.

    Uses ``type.__module__`` string inspection - never imports Pandas or
    GeoPandas. Called from ``ExecutionResult.__post_init__`` so it covers
    all construction paths (direct and via ``from_executor_dict``).
    """
    for key, value in payload.items():
        t = type(value)
        base_module = (getattr(t, "__module__", None) or "").split(".")[0]
        if base_module in _UNSAFE_PAYLOAD_MODULES:
            raise ValueError(
                f"ExecutionResult.payload[{key!r}] carries {t.__qualname__!r} "
                f"from {base_module!r} - DataFrames are forbidden in payload. "
                "Store row counts, paths, or result_ids instead."
            )
        if isinstance(value, (bytes, bytearray)):
            raise ValueError(
                f"ExecutionResult.payload[{key!r}] carries raw "
                f"{t.__name__!r} - binary blobs are forbidden in payload."
            )
        if callable(getattr(value, "read", None)) and not isinstance(value, (str, bytes)):
            raise ValueError(
                f"ExecutionResult.payload[{key!r}] is a file-like stream "
                f"({t.__name__!r}) - forbidden in payload."
            )


# Typed payload value objects


@dataclass(frozen=True)
class ArtifactPayload:
    """Typed payload for standard artifact execution success results.

    Covers the common reference keys shared by all three executors:
    ``result_id`` (blob stored in DB), ``output_path`` (file on disk),
    ``filename``, ``format``, ``rows_count``, ``run_id``.

    Extra executor-specific keys (e.g. ``user_error``, ``error_node_id``)
    are not covered here; they stay in ``ExecutionResult.payload`` as raw
    dict entries until they acquire their own typed representation.

    Boundary: leaf contract - no Pandas, DB, FastAPI, or ArcGIS imports.

    Go-ready mapping::

        type ArtifactPayload struct {
            ResultID   *string
            OutputPath *string
            Filename   *string
            Format     *string
            RowsCount  *int
            RunID      *string
        }
    """

    result_id: Optional[str] = None
    output_path: Optional[str] = None
    filename: Optional[str] = None
    format: Optional[str] = None
    rows_count: Optional[int] = None
    run_id: Optional[str] = None

    @classmethod
    def from_payload_dict(cls, d: Dict[str, Any]) -> "ArtifactPayload":
        """Extract known artifact keys from a payload dict.

        Unknown keys are silently ignored; the caller retains them in
        ``ExecutionResult.payload`` for backward compatibility.
        """
        return cls(
            result_id=d.get("result_id") or None,
            output_path=d.get("output_path") or None,
            filename=d.get("filename") or None,
            format=d.get("format") or None,
            rows_count=d.get("rows_count"),
            run_id=d.get("run_id") or None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return only non-None fields for dict-compat reconstruction."""
        out: Dict[str, Any] = {}
        if self.result_id is not None:
            out["result_id"] = self.result_id
        if self.output_path is not None:
            out["output_path"] = self.output_path
        if self.filename is not None:
            out["filename"] = self.filename
        if self.format is not None:
            out["format"] = self.format
        if self.rows_count is not None:
            out["rows_count"] = self.rows_count
        if self.run_id is not None:
            out["run_id"] = self.run_id
        return out
