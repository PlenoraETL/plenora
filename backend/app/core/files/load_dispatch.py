"""Compatibility wrapper for DataFrame parser dispatch."""

from app.core.dataframes.load_dispatch import (
    DataLoaderProtocol,
    FrameIOEngine,
    LoadedFrame,
    LoggerProtocol,
    load_by_format,
    load_frame_by_format,
)


__all__ = [
    "DataLoaderProtocol",
    "FrameIOEngine",
    "LoadedFrame",
    "LoggerProtocol",
    "load_by_format",
    "load_frame_by_format",
]
