"""Shared JSON payload aliases for the arricchisci boundary."""

from __future__ import annotations

from typing import Any

JsonObject = dict[str, Any]
JsonRows = list[JsonObject]
JsonObjectById = dict[str, JsonObject]
IndexGroups = dict[str, list[int]]
StringMap = dict[str, str]
