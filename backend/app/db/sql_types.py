"""Shared SQL translator typing contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeAlias


SqlParamMap: TypeAlias = Mapping[str, Any]
SqlTranslationMap: TypeAlias = Mapping[str, str]
QueryParams: TypeAlias = Mapping[str, Any]
QueryTimings: TypeAlias = dict[str, float]
