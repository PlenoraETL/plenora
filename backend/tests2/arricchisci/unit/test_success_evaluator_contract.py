"""Contracts for REST connector application-level success evaluation."""

from __future__ import annotations

import pytest

from app.core.arricchisci.success_evaluator import evaluate_success


pytestmark = [
    pytest.mark.unit,
    pytest.mark.arricchisci,
    pytest.mark.contract,
    pytest.mark.regression,
]


def test_error_path_zero_is_application_error():
    evaluation = evaluate_success({"error_code": 0}, {"error_path": "error_code"})

    assert evaluation.ok is False
    assert evaluation.error_message == "0"


def test_error_message_zero_is_preserved():
    evaluation = evaluate_success(
        {"error": {"message": 0}},
        {"error_path": "error"},
    )

    assert evaluation.ok is False
    assert evaluation.error_message == "0"
