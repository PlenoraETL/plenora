"""Shared payload aliases for the projects boundary."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

JsonObject = dict[str, Any]
OwnershipResource = dict[str, object]
CanvasResourceRefs = dict[str, set[str]]
StringMap = dict[str, str]
ColumnMetadataRows = list[StringMap]
CleanupReport = dict[str, int]
ProjectUsageRow = dict[str, object]
ProjectUsageRows = list[ProjectUsageRow]
SchemaRows = list[JsonObject]
SchemaMap = dict[str, SchemaRows]
EdgesByTarget = dict[str, list[Any]]
TransformHandlerRegistry = dict[str, Callable[..., list[Any]]]
