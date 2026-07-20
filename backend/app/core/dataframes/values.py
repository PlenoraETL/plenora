"""Scalar and frame probes for the current pandas-backed dataframe engine."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def is_null_scalar(value: Any) -> bool:
    """Return True for scalar null values without treating lists/dicts as null."""
    if isinstance(value, (list, dict)):
        return False
    try:
        return bool(pd.isna(value))
    except Exception:
        return False


def to_python_scalar(value: Any) -> Any:
    """Convert common numpy scalar values to Python builtins."""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


def to_python_datetime(value: Any) -> Any:
    """Convert pandas/numpy datetime scalars to Python ``datetime`` values."""
    if isinstance(value, np.datetime64):
        value = pd.Timestamp(value)
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime(warn=False)
    return value


def is_dataframe(value: Any) -> bool:
    """Return True when value is a pandas DataFrame."""
    return isinstance(value, pd.DataFrame)


def is_nonempty_dataframe(value: Any) -> bool:
    """Return True when value is a pandas DataFrame with at least one row."""
    return isinstance(value, pd.DataFrame) and not value.empty


def is_empty_dataframe(value: Any) -> bool:
    """Return True when value is an empty pandas DataFrame."""
    return isinstance(value, pd.DataFrame) and value.empty


def is_geometry_series(value: Any) -> bool:
    """Return True for GeoSeries-like values without importing geopandas."""
    return hasattr(value, "geom_type")


__all__ = [
    "is_dataframe",
    "is_empty_dataframe",
    "is_geometry_series",
    "is_nonempty_dataframe",
    "is_null_scalar",
    "to_python_datetime",
    "to_python_scalar",
]
