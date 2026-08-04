"""Compatibility exports for the native XLSX I/O boundary.

The path-only implementation lives in :mod:`app.core.io.native_xlsx` so local
filesystem capabilities remain confined to the declared I/O boundary.
"""

from app.core.io.native_xlsx import (
    NativeXlsxUnsupported,
    try_write_dataframe_to_xlsx_stream,
)

__all__ = [
    "NativeXlsxUnsupported",
    "try_write_dataframe_to_xlsx_stream",
]
