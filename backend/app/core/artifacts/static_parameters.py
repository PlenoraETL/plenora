"""Typed static-parameter processing for StaticValues/API parameters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, TypeAlias

from app.utils.logger import get_logger
from app.utils.template_variables import process_template_variables

logger = get_logger(__name__)

StaticParameterValues: TypeAlias = dict[str, Any]


@dataclass(frozen=True)
class StaticParameterField:
    """One StaticValues/API parameter field from canvas or API config."""

    name: str
    value: Any = ""
    field_type: str = "string"

    @classmethod
    def from_raw(cls, raw: Mapping[str, Any] | "StaticParameterField") -> "StaticParameterField":
        if isinstance(raw, StaticParameterField):
            return raw
        return cls(
            name=str(raw.get("name") or ""),
            value=raw.get("value", ""),
            field_type=str(raw.get("field_type") or "string"),
        )


def _cast_static_value(value: Any, field_type: str, field_name: str) -> Any:
    try:
        if field_type == "integer":
            return int(float(value)) if value not in (None, "") else None
        if field_type == "float":
            return float(value) if value not in (None, "") else None
        if field_type == "boolean":
            if isinstance(value, str):
                return value.lower() in ("true", "1", "si", "yes")
            return bool(value)
    except (ValueError, TypeError) as exc:
        logger.debug(
            "Cast tipo %r fallito per campo %r, mantenuto come stringa: %s",
            field_type,
            field_name,
            exc,
        )
    return value


def process_static_fields(
    fields: Iterable[Mapping[str, Any] | StaticParameterField],
    reference_date: datetime | None = None,
) -> StaticParameterValues:
    """Resolve template variables and explicit field types for static fields."""

    result: StaticParameterValues = {}

    for raw_field in fields:
        field = StaticParameterField.from_raw(raw_field)
        if not field.name:
            continue

        processed = process_template_variables(field.value, reference_date)
        result[field.name] = _cast_static_value(
            processed,
            field.field_type,
            field.name,
        )

    return result
