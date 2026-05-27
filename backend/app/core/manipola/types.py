"""Shared payload aliases for the manipola boundary."""

from __future__ import annotations

from typing import Any

JsonObject = dict[str, Any]
JsonRows = list[JsonObject]
StringMap = dict[str, str]
StringSetMap = dict[str, set[str]]
ColumnMetadataRows = list[StringMap]
FrameMap = dict[str, Any]
RegistryMap = dict[str, JsonObject]
RegistryRows = list[JsonObject]
