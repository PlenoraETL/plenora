"""Common payload aliases for typed API and boundary annotations."""

from __future__ import annotations

from typing import Any


JsonObject = dict[str, Any]
JsonRows = list[JsonObject]
JsonObjectById = dict[str, JsonObject]
StringMap = dict[str, str]
StringRows = list[StringMap]
ObjectMap = dict[str, object]
ObjectRows = list[ObjectMap]
IntMap = dict[str, int]
HeadersMap = dict[str, str]
