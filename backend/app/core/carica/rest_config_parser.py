"""Parse CARICA REST destination node data into typed sink configuration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Sequence
from urllib.parse import urlparse

from app.core.connections.rest_sink import (
    RestSinkBatchMode,
    RestSinkBodyField,
    RestSinkBodyMapping,
    RestSinkConfig,
    RestSinkHeader,
    RestSinkMethod,
)
from app.core.security.outbound_url import validate_external_url


_HEADER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]+$")
_DEFAULT_SUCCESS_STATUSES = (200, 201, 202, 204)


@dataclass(frozen=True)
class ParsedCaricaRestConfig:
    """Typed REST config plus log-safe metadata derived from CARICA node data."""

    sink_config: RestSinkConfig
    redacted_headers: Mapping[str, str]


def parse_carica_rest_config(carica_node_data: Mapping[str, object]) -> ParsedCaricaRestConfig:
    """Build a typed REST sink config from CARICA canvas node data.

    Supported shape is intentionally tolerant while the UI is being wired:
    REST fields may be placed at the node root or under ``rest_config``.
    Invalid URLs, methods, header shapes and numeric settings fail closed with
    ``ValueError`` before any worker can attempt a write.
    """

    rest_data = _rest_data(carica_node_data)
    headers = _parse_headers(rest_data)
    body_mapping = _parse_body_mapping(_mapping_value(rest_data, "body_mapping") or {})
    sink_config = RestSinkConfig(
        url=_parse_url(rest_data),
        method=_parse_method(rest_data),
        headers=headers,
        body_mapping=body_mapping,
        batch_mode=_parse_batch_mode(rest_data),
        batch_size=_parse_positive_int(rest_data, ("batch_size", "rest_batch_size"), 100),
        timeout_seconds=_parse_positive_float(
            rest_data,
            ("timeout_seconds", "timeout", "rest_timeout_seconds"),
            30.0,
        ),
        success_statuses=_parse_success_statuses(rest_data),
    )
    return ParsedCaricaRestConfig(
        sink_config=sink_config,
        redacted_headers={
            header.name: "***REDACTED***" if header.secret else header.value
            for header in headers
        },
    )


def _rest_data(carica_node_data: Mapping[str, object]) -> Mapping[str, object]:
    nested = carica_node_data.get("rest_config")
    if nested is None:
        nested = carica_node_data.get("rest")
    if nested is None:
        return carica_node_data
    if not isinstance(nested, Mapping):
        raise ValueError("CARICA REST config must be an object")
    return nested


def _parse_url(rest_data: Mapping[str, object]) -> str:
    value = _string_value(rest_data, ("url", "rest_url", "endpoint_url"))
    if value is None:
        raise ValueError("CARICA REST url is required")

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("CARICA REST url must be absolute http/https")
    if parsed.username or parsed.password:
        raise ValueError("CARICA REST url must not include credentials")
    try:
        validate_external_url(value)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    return value


def _parse_method(rest_data: Mapping[str, object]) -> RestSinkMethod:
    value = (_string_value(rest_data, ("method", "rest_method")) or "POST").upper()
    try:
        return RestSinkMethod(value)
    except ValueError as exc:
        allowed = ", ".join(method.value for method in RestSinkMethod)
        raise ValueError(f"CARICA REST method must be one of: {allowed}") from exc


def _parse_batch_mode(rest_data: Mapping[str, object]) -> RestSinkBatchMode:
    value = (
        _string_value(rest_data, ("batch_mode", "rest_batch_mode"))
        or RestSinkBatchMode.BATCH.value
    ).lower()
    try:
        return RestSinkBatchMode(value)
    except ValueError as exc:
        allowed = ", ".join(mode.value for mode in RestSinkBatchMode)
        raise ValueError(f"CARICA REST batch_mode must be one of: {allowed}") from exc


def _parse_headers(rest_data: Mapping[str, object]) -> tuple[RestSinkHeader, ...]:
    secret_names = _parse_secret_header_names(rest_data)
    raw_headers = rest_data.get("headers")
    if raw_headers is None:
        raw_headers = rest_data.get("rest_headers")
    if raw_headers is None:
        return ()

    headers: list[RestSinkHeader] = []
    if isinstance(raw_headers, Mapping):
        for name, value in raw_headers.items():
            header_name = _require_header_name(name)
            headers.append(
                RestSinkHeader(
                    name=header_name,
                    value=_require_header_value(value),
                    secret=header_name.lower() in secret_names,
                )
            )
        return tuple(headers)

    if isinstance(raw_headers, Sequence) and not isinstance(raw_headers, str):
        for item in raw_headers:
            if not isinstance(item, Mapping):
                raise ValueError("CARICA REST header entries must be objects")
            name = _require_header_name(item.get("name"))
            value = _require_header_value(item.get("value"))
            secret_value = item.get("secret")
            if secret_value is not None and not isinstance(secret_value, bool):
                raise ValueError("CARICA REST header secret flag must be boolean")
            headers.append(
                RestSinkHeader(
                    name=name,
                    value=value,
                    secret=(secret_value is True) or name.lower() in secret_names,
                )
            )
        return tuple(headers)

    raise ValueError("CARICA REST headers must be an object or list")


def _parse_secret_header_names(rest_data: Mapping[str, object]) -> set[str]:
    raw = rest_data.get("secret_headers")
    if raw is None:
        raw = rest_data.get("rest_secret_headers")
    if raw is None:
        return set()
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError("CARICA REST secret_headers must be a list")
    return {_require_header_name(name).lower() for name in raw}


def _parse_body_mapping(mapping_data: Mapping[str, object]) -> RestSinkBodyMapping:
    fields_raw = mapping_data.get("fields", ())
    if not isinstance(fields_raw, Sequence) or isinstance(fields_raw, str):
        raise ValueError("CARICA REST body_mapping.fields must be a list")

    fields: list[RestSinkBodyField] = []
    for field_data in fields_raw:
        if not isinstance(field_data, Mapping):
            raise ValueError("CARICA REST body mapping field entries must be objects")
        source = _string_field(field_data, ("source", "source_column"), "body mapping source")
        target = _string_field(field_data, ("target", "dest", "dest_column"), "body mapping target")
        required = field_data.get("required", False)
        if not isinstance(required, bool):
            raise ValueError("CARICA REST body mapping required flag must be boolean")
        default = field_data.get("default")
        if not _is_rest_scalar(default):
            raise ValueError("CARICA REST body mapping default must be a scalar")
        fields.append(
            RestSinkBodyField(
                source=source,
                target=target,
                required=required,
                default=default,
            )
        )

    include_unmapped = mapping_data.get("include_unmapped", True)
    if not isinstance(include_unmapped, bool):
        raise ValueError("CARICA REST body_mapping.include_unmapped must be boolean")

    batch_records_key = _string_field(
        mapping_data,
        ("batch_records_key", "records_key"),
        "body mapping batch_records_key",
        default="records",
    )
    return RestSinkBodyMapping(
        fields=tuple(fields),
        include_unmapped=include_unmapped,
        batch_records_key=batch_records_key,
    )


def _parse_success_statuses(rest_data: Mapping[str, object]) -> tuple[int, ...]:
    raw = rest_data.get("success_statuses")
    if raw is None:
        raw = rest_data.get("rest_success_statuses")
    if raw is None:
        return _DEFAULT_SUCCESS_STATUSES
    if not isinstance(raw, Sequence) or isinstance(raw, str):
        raise ValueError("CARICA REST success_statuses must be a list")

    statuses: list[int] = []
    for item in raw:
        status = _coerce_int(item, "CARICA REST success status")
        if status < 100 or status > 599:
            raise ValueError("CARICA REST success status must be a valid HTTP status")
        statuses.append(status)
    if not statuses:
        raise ValueError("CARICA REST success_statuses cannot be empty")
    return tuple(statuses)


def _parse_positive_int(
    rest_data: Mapping[str, object],
    names: tuple[str, ...],
    default: int,
) -> int:
    raw = _first_value(rest_data, names)
    if raw is None:
        return default
    value = _coerce_int(raw, f"CARICA REST {names[0]}")
    if value < 1:
        raise ValueError(f"CARICA REST {names[0]} must be >= 1")
    return value


def _parse_positive_float(
    rest_data: Mapping[str, object],
    names: tuple[str, ...],
    default: float,
) -> float:
    raw = _first_value(rest_data, names)
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValueError(f"CARICA REST {names[0]} must be numeric")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"CARICA REST {names[0]} must be numeric") from exc
    if value <= 0:
        raise ValueError(f"CARICA REST {names[0]} must be > 0")
    return value


def _mapping_value(rest_data: Mapping[str, object], name: str) -> Mapping[str, object] | None:
    raw = rest_data.get(name)
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise ValueError(f"CARICA REST {name} must be an object")
    return raw


def _string_value(rest_data: Mapping[str, object], names: tuple[str, ...]) -> str | None:
    raw = _first_value(rest_data, names)
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"CARICA REST {names[0]} must be a non-empty string")
    return raw.strip()


def _string_field(
    data: Mapping[str, object],
    names: tuple[str, ...],
    label: str,
    default: str | None = None,
) -> str:
    raw = _first_value(data, names)
    if raw is None and default is not None:
        return default
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"CARICA REST {label} must be a non-empty string")
    return raw.strip()


def _first_value(data: Mapping[str, object], names: tuple[str, ...]) -> object | None:
    for name in names:
        if name in data:
            return data[name]
    return None


def _require_header_name(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("CARICA REST header name must be a non-empty string")
    header_name = value.strip()
    if not _HEADER_NAME_PATTERN.fullmatch(header_name):
        raise ValueError("CARICA REST header name is invalid")
    return header_name


def _require_header_value(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("CARICA REST header value must be a string")
    if "\r" in value or "\n" in value:
        raise ValueError("CARICA REST header value cannot contain newlines")
    return value


def _coerce_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc


def _is_rest_scalar(value: object) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))
