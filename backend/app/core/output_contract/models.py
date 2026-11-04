"""Versioned JSON models for project output contracts.

These models deliberately use only JSON-native fields so the contract can be
ported to Go structs without carrying canvas/Pydantic/ReactFlow details.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutputColumnType(BaseModel):
    model_config = ConfigDict(extra="forbid")

    logical: Literal["string", "integer", "number", "boolean", "datetime", "geometry"] = "string"
    nullable: bool = True


class OutputColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_name: str
    source_node: str | None = None
    physical_name: str
    display_name: str
    type: OutputColumnType = Field(default_factory=OutputColumnType)
    ordinal: int
    status: Literal["active"] = "active"


class OutputContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    producer_project_id: str
    revision: int = 1
    updated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    columns: list[OutputColumn] = Field(default_factory=list)
