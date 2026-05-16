"""Shared payload aliases for the estrai boundary."""

from __future__ import annotations

from typing import Any

JsonObject = dict[str, Any]
StringMap = dict[str, str]
ColumnMetadataRows = list[StringMap]
DatabaseSchema = dict[str, list[Any]]

