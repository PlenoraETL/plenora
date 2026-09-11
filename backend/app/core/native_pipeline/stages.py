"""Stage identity of the native sidecar chain.

The chain is fixed: one database read, one Data plan application, one IO
conversion. Stages are named here (and nowhere else) so error payloads, stage
reports and the CLI document share a single vocabulary.
"""

from __future__ import annotations

from enum import Enum


class PipelineStage(str, Enum):
    """A single subprocess step of the native chain."""

    DATABASE_READ = "database.read"
    DATA_PLAN = "data.plan"
    IO_CONVERT = "io.convert"


__all__ = ["PipelineStage"]
