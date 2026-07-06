"""Application-level success evaluation for REST connector responses."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.shared.resources.json_path import extract_json_value


@dataclass(frozen=True)
class SuccessEvaluation:
    ok: bool
    error_message: str | None = None


def success_config_from_api_config(api_config: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read success evaluation config from top-level or response config."""

    cfg = dict(api_config or {})
    response_cfg = cfg.get("response")
    if isinstance(response_cfg, Mapping):
        merged = {
            key: response_cfg[key]
            for key in ("success_when", "error_path")
            if key in response_cfg
        }
    else:
        merged = {}
    for key in ("success_when", "error_path"):
        if key in cfg:
            merged[key] = cfg[key]
    return merged


def evaluate_success(data: Any, config: Mapping[str, Any] | None) -> SuccessEvaluation:
    """Evaluate application-level success rules against parsed response data."""

    cfg = dict(config or {})
    error_path = cfg.get("error_path")
    if error_path:
        error_value = extract_json_value(data, str(error_path))
        if error_value:
            return SuccessEvaluation(False, _format_error_value(error_value))

    success_when = cfg.get("success_when")
    if success_when:
        if not _matches_success_when(data, success_when):
            return SuccessEvaluation(False, "success_when non soddisfatto")

    return SuccessEvaluation(True)


def _matches_success_when(data: Any, condition: Any) -> bool:
    if isinstance(condition, list):
        return all(_matches_success_when(data, item) for item in condition)
    if not isinstance(condition, Mapping):
        return bool(condition)

    path = condition.get("path")
    value = extract_json_value(data, str(path)) if path else data

    if "exists" in condition:
        return (value is not None) is bool(condition["exists"])
    if "truthy" in condition:
        return bool(value) is bool(condition["truthy"])
    if "equals" in condition:
        return value == condition["equals"]
    if "not_equals" in condition:
        return value != condition["not_equals"]
    if "in" in condition:
        candidates = condition["in"]
        return isinstance(candidates, list) and value in candidates
    if "not_in" in condition:
        candidates = condition["not_in"]
        return isinstance(candidates, list) and value not in candidates
    return bool(value)


def _format_error_value(error_value: Any) -> str:
    if isinstance(error_value, Mapping):
        for key in ("detail", "error", "message"):
            value = error_value.get(key)
            if value:
                return str(value)
    return str(error_value)
