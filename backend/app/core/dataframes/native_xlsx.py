"""DataFrame-specific adapter for the native XLSX I/O boundary.

Pandas schema inference and CSV serialization remain in the dataframe domain.
The path-only exporter lifecycle is delegated to :mod:`app.core.io.native_xlsx`.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping, TypedDict

from app.core.dataframes.export_writer import (
    NativeXlsxUnsupported,
    _native_xlsx_column_type,
    _native_xlsx_has_multiindex_columns,
)
from app.core.io import native_xlsx as native_xlsx_io


class _NativeColumnSchema(TypedDict):
    name: str
    type: str


class _NativeExportSchema(TypedDict):
    schema_version: int
    sheet_name: str
    columns: list[_NativeColumnSchema]
    delimiter: str
    null_token: str
    row_count_hint: int
    temp_dir: str


def _declared_type_for_column(
    column: Any,
    column_types: Mapping[str, Any] | None,
) -> str:
    if not column_types:
        return ""
    name = str(column)
    direct = column_types.get(name)
    if direct is not None:
        return str(direct).strip().lower()
    lowered = name.lower()
    for candidate, raw_type in column_types.items():
        if str(candidate).lower() == lowered:
            return str(raw_type or "").strip().lower()
    return ""


def _build_schema(
    df: Any,
    *,
    sheet_name: str,
    column_types: Mapping[str, Any] | None,
    null_token: str,
    temp_dir: Path,
) -> _NativeExportSchema:
    if _native_xlsx_has_multiindex_columns(df):
        raise NativeXlsxUnsupported("le colonne MultiIndex richiedono il writer Python")
    if any(not isinstance(column, str) for column in df.columns):
        raise NativeXlsxUnsupported(
            "le intestazioni non testuali richiedono il writer Python"
        )

    columns: list[_NativeColumnSchema] = []
    for index, name in enumerate(df.columns):
        series = df.iloc[:, index]
        declared_type = _declared_type_for_column(name, column_types)
        columns.append(
            {
                "name": name,
                "type": _native_xlsx_column_type(series, declared_type),
            }
        )
    return {
        "schema_version": 1,
        "sheet_name": sheet_name,
        "columns": columns,
        "delimiter": ",",
        "null_token": null_token,
        "row_count_hint": len(df),
        "temp_dir": str(temp_dir),
    }


def try_write_dataframe_to_xlsx_stream(
    df: Any,
    sink: Any,
    *,
    sheet_name: str = "Sheet1",
    column_types: Mapping[str, Any] | None = None,
) -> bool:
    """Try the native exporter while keeping pandas behind its boundary."""

    def build_schema(temp_dir: Path, null_token: str) -> Mapping[str, object]:
        return _build_schema(
            df,
            sheet_name=sheet_name,
            column_types=column_types,
            null_token=null_token,
            temp_dir=temp_dir,
        )

    def write_csv(buffer: io.TextIOBase, null_token: str) -> None:
        df.to_csv(buffer,
            index=False,
            na_rep=null_token,
            lineterminator="\n",
            encoding="utf-8",
        )

    return native_xlsx_io.try_write_native_xlsx_stream(
        sink,
        build_schema=build_schema,
        write_csv=write_csv,
    )


__all__ = [
    "NativeXlsxUnsupported",
    "try_write_dataframe_to_xlsx_stream",
]
