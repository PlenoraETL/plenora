"""Contract tests for REST connector application-level success evaluation."""

from __future__ import annotations

import pytest

from app.core.arricchisci.success_evaluator import (
    evaluate_success,
    success_config_from_api_config,
)


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
]


def test_empty_config_keeps_response_successful():
    result = evaluate_success({"status": "error"}, {})
    assert result.ok is True


def test_error_path_turns_truthy_error_into_failure():
    result = evaluate_success(
        {"error": {"message": "quota exceeded"}},
        {"error_path": "error"},
    )
    assert result.ok is False
    assert result.error_message == "quota exceeded"


def test_success_when_equals_must_match():
    result = evaluate_success(
        {"status": "failed"},
        {"success_when": {"path": "status", "equals": "ok"}},
    )
    assert result.ok is False


def test_success_when_list_requires_all_conditions():
    result = evaluate_success(
        {"status": "ok", "ready": True},
        {
            "success_when": [
                {"path": "status", "equals": "ok"},
                {"path": "ready", "truthy": True},
            ]
        },
    )
    assert result.ok is True


def test_top_level_success_config_overrides_response_config():
    cfg = success_config_from_api_config({
        "response": {"error_path": "error"},
        "error_path": "detail",
    })
    assert cfg == {"error_path": "detail"}
